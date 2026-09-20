"""Tests for the skill catalogue: packaged scan, runtime dir, git install."""

import subprocess

from cloudbot.agent import skills

_SKILL_MD = (
    "---\nname: {}\ndescription: a test skill\n---\n\n"
    "Fetch the linked audio and transcribe it to MIDI.\n"
)


def _write_skill(
    root, name: str, body: str = "Fetch the linked audio and transcribe it."
) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: a test skill\n---\n\n{body}\n",
        encoding="utf-8",
    )


class TestScan:
    def test_packaged_skills_are_indexed(self, mock_bot):
        assert "- song-to-midi:" in skills.skill_index(bot=mock_bot)

    def test_runtime_dir_defaults_to_the_data_path(self, mock_bot):
        assert skills.skills_root(mock_bot) == mock_bot.data_path / "skills"

    def test_configured_dir_overrides_the_default(self, mock_bot):
        mock_bot.config["plugins"] = {
            "agent": {"skills": {"dir": "/tmp/somewhere"}}
        }
        assert str(skills.skills_root(mock_bot)).endswith("/tmp/somewhere")

    def test_runtime_skill_folder_is_indexed_and_lazy_loaded(self, mock_bot):
        body = "Fetch the linked audio and transcribe it."
        _write_skill(mock_bot.data_path / "skills", "external-thing", body)
        index = skills.skill_index(bot=mock_bot)
        assert "- external-thing: a test skill" in index
        assert skills.read_skill_body("external-thing", mock_bot) == body

    def test_flat_markdown_is_indexed(self, mock_bot):
        root = mock_bot.data_path / "skills"
        root.mkdir(parents=True)
        (root / "flat-skill.md").write_text(_SKILL_MD.format("flat-skill"))
        assert "- flat-skill: a test skill" in skills.skill_index(bot=mock_bot)

    def test_a_missing_runtime_dir_is_simply_empty(self, mock_bot):
        summary = skills.skills_status(mock_bot)[0]
        assert summary.startswith("Skills: ")
        assert summary.endswith("0 external")

    def test_an_undecodable_markdown_is_skipped_not_fatal(self, mock_bot):
        root = mock_bot.data_path / "skills"
        root.mkdir(parents=True)
        (root / "good.md").write_text(_SKILL_MD.format("good-skill"))
        (root / "bad.md").write_bytes(b"\xff\xfe windows \x92 quotes")
        index = skills.skill_index(bot=mock_bot)
        assert "- good-skill:" in index
        assert "bad.md" not in index

    def test_the_scan_reads_one_level_only(self, mock_bot):
        nested = mock_bot.data_path / "skills" / "checkout" / "docs"
        nested.mkdir(parents=True)
        (nested / "not-a-skill.md").write_text(_SKILL_MD.format("nested"))
        assert "not-a-skill" not in skills.skill_index(bot=mock_bot)

    def test_hidden_staging_dirs_are_ignored(self, mock_bot):
        staging = mock_bot.data_path / "skills" / ".staging-abc"
        _write_skill(staging, "half-installed")
        assert "half-installed" not in skills.skill_index(bot=mock_bot)


class TestVisibility:
    def test_external_skills_stay_main_agent_only(self, mock_bot):
        _write_skill(mock_bot.data_path / "skills", "external-thing")
        assert "- external-thing:" in skills.skill_index(bot=mock_bot)
        assert "- external-thing:" not in skills.skill_index("kaggle", mock_bot)

    def test_frontmatter_agents_widen_visibility(self, mock_bot):
        root = mock_bot.data_path / "skills"
        folder = root / "shared-thing"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            "---\nname: shared-thing\ndescription: x\nagents: [kaggle]\n---\n\ny\n"
        )
        assert "- shared-thing:" in skills.skill_index("kaggle", mock_bot)

    def test_status_lists_external_sources(self, mock_bot):
        _write_skill(mock_bot.data_path / "skills", "external-thing")
        status = "\n".join(skills.skills_status(mock_bot))
        assert "- external-thing:" in status
        assert "packaged: " in status


class TestAssets:
    def _skill_with_assets(self, mock_bot) -> None:
        root = mock_bot.data_path / "skills" / "external-thing"
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(
            "---\nname: external-thing\ndescription: a test skill\n"
            "---\n\nFetch the linked audio and transcribe it.\n",
            encoding="utf-8",
        )
        (root / "schemas").mkdir()
        (root / "schemas" / "thing.schema.json").write_text(
            '{"type": "object"}', encoding="utf-8"
        )
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("secret", encoding="utf-8")

    def test_a_skill_file_is_readable(self, mock_bot):
        self._skill_with_assets(mock_bot)
        result = skills.read_skill_asset(
            "external-thing", "schemas/thing.schema.json", mock_bot
        )
        assert result == '{"type": "object"}'

    def test_a_directory_path_lists_contents_with_sizes(self, mock_bot):
        self._skill_with_assets(mock_bot)
        listing = skills.read_skill_asset("external-thing", ".", mock_bot)
        assert "schemas/" in listing
        assert "thing.schema.json (18 B)" in listing
        assert ".git" not in listing

    def test_names_outside_the_spec_rules_are_rejected(self, mock_bot):
        for hostile in ("..", ".", "../x", "a/b", "Uppercase", "under_score"):
            assert "not a skill name" in skills.remove_skill(hostile, mock_bot)

    def test_traversal_is_rejected(self, mock_bot):
        self._skill_with_assets(mock_bot)
        for hostile in ("../../etc", "../.."):
            result = skills.read_skill_asset(
                "external-thing", hostile, mock_bot
            )
            assert "escapes" in result or "hidden" in result

    def test_hidden_paths_are_unreadable(self, mock_bot):
        self._skill_with_assets(mock_bot)
        assert "hidden" in skills.read_skill_asset(
            "external-thing", ".git/config", mock_bot
        )

    def test_dangling_and_looping_symlinks_do_not_break_listings(
        self, mock_bot
    ):
        self._skill_with_assets(mock_bot)
        root = mock_bot.data_path / "skills" / "external-thing"
        (root / "dangling").symlink_to(root / "nowhere")
        (root / "loop").symlink_to(root)
        listing = skills.read_skill_asset("external-thing", ".", mock_bot)
        assert "schemas/" in listing
        assert "dangling" not in listing
        assert "loop" not in listing

    def test_a_flat_skill_has_no_folder(self, mock_bot):
        root = mock_bot.data_path / "skills"
        root.mkdir(parents=True)
        (root / "flat-skill.md").write_text(
            _SKILL_MD.format("flat-skill"), encoding="utf-8"
        )
        assert "no folder" in skills.read_skill_asset(
            "flat-skill", "anything.txt", mock_bot
        )

    def test_packaged_skills_read_from_their_group_dir(self, mock_bot):
        result = skills.read_skill_asset(
            "song-to-midi-gpu", "song-to-midi-gpu.md", mock_bot
        )
        assert "midifier" in result.lower()


def _git_repo(path, tag: str | None = None) -> None:
    _write_skill(path, "archify", "Render diagrams.")
    (path / "README.md").write_text("# not a skill\n", encoding="utf-8")
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(git + ["init", "-q"], cwd=path, check=True)
    subprocess.run(git + ["add", "-A"], cwd=path, check=True)
    subprocess.run(git + ["commit", "-qm", "init"], cwd=path, check=True)
    if tag:
        subprocess.run(git + ["tag", tag], cwd=path, check=True)


class TestInstall:
    def test_install_relocates_a_nested_skill_folder(self, mock_bot, tmp_path):
        source = tmp_path / "source-repo"
        _git_repo(source)
        result = skills.install_from_git(f"file://{source}", "", mock_bot)
        assert result.startswith("Installed skill 'archify'")
        root = mock_bot.data_path / "skills"
        assert (root / "archify" / "SKILL.md").is_file()
        assert not list(root.glob(".staging-*"))
        assert skills.read_skill_body("archify", mock_bot) == "Render diagrams."

    def test_install_a_tag_pins_the_clone(self, mock_bot, tmp_path):
        source = tmp_path / "source-repo"
        _git_repo(source, tag="v1.2.3")
        result = skills.install_from_git(f"file://{source}", "v1.2.3", mock_bot)
        assert "v1.2.3" in result

    def test_reinstall_replaces_the_existing_folder(self, mock_bot, tmp_path):
        source = tmp_path / "source-repo"
        _git_repo(source)
        skills.install_from_git(f"file://{source}", "", mock_bot)
        (mock_bot.data_path / "skills" / "archify" / "SKILL.md").write_text(
            "tampered", encoding="utf-8"
        )
        skills.install_from_git(f"file://{source}", "", mock_bot)
        assert skills.read_skill_body("archify", mock_bot) == "Render diagrams."

    def test_a_repo_without_skill_md_fails_cleanly(self, mock_bot, tmp_path):
        bare = tmp_path / "bare-repo"
        bare.mkdir()
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(git + ["init", "-q"], cwd=bare, check=True)
        (bare / "README.md").write_text("no skills", encoding="utf-8")
        subprocess.run(git + ["add", "-A"], cwd=bare, check=True)
        subprocess.run(git + ["commit", "-qm", "init"], cwd=bare, check=True)
        result = skills.install_from_git(f"file://{bare}", "", mock_bot)
        assert "no SKILL.md" in result
        assert not list((mock_bot.data_path / "skills").glob(".staging-*"))

    def test_install_of_an_undecodable_skill_md_fails_cleanly(
        self, mock_bot, tmp_path
    ):
        source = tmp_path / "mojibake-repo"
        folder = source / "mojibake"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_bytes(
            b"---\nname: mojibake\ndescription: \x92windows\x92\n---\n\ny\n"
        )
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(git + ["init", "-q"], cwd=source, check=True)
        subprocess.run(git + ["add", "-A"], cwd=source, check=True)
        subprocess.run(git + ["commit", "-qm", "init"], cwd=source, check=True)
        result = skills.install_from_git(f"file://{source}", "", mock_bot)
        assert "no readable frontmatter" in result
        assert not list((mock_bot.data_path / "skills").glob(".staging-*"))

    def test_remove_deletes_the_skill(self, mock_bot, tmp_path):
        source = tmp_path / "source-repo"
        _git_repo(source)
        skills.install_from_git(f"file://{source}", "", mock_bot)
        assert skills.remove_skill("archify", mock_bot).startswith("Removed")
        assert skills.read_skill_body("archify", mock_bot) is None

    def test_remove_an_unknown_name_fails_cleanly(self, mock_bot):
        assert "no external skill" in skills.remove_skill("nope", mock_bot)

    def test_remove_rejects_names_that_escape_the_dir(self, mock_bot):
        skills_root = mock_bot.data_path / "skills"
        skills_root.mkdir(parents=True)
        (skills_root / "real").mkdir()
        for hostile in ("..", ".", "../x", "a/b"):
            assert "not a skill name" in skills.remove_skill(hostile, mock_bot)
        assert mock_bot.data_path.is_dir()
        assert (skills_root / "real").is_dir()

    def test_install_contains_a_hostile_frontmatter_name(
        self, mock_bot, tmp_path
    ):
        source = tmp_path / "evil-repo"
        folder = source / "evil"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            "---\nname: ../../evil\ndescription: x\n---\n\ny\n",
            encoding="utf-8",
        )
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(git + ["init", "-q"], cwd=source, check=True)
        subprocess.run(git + ["add", "-A"], cwd=source, check=True)
        subprocess.run(git + ["commit", "-qm", "init"], cwd=source, check=True)
        result = skills.install_from_git(f"file://{source}", "", mock_bot)
        # The unsafe frontmatter name is replaced by the safe folder name, so
        # the install stays inside the runtime dir.
        assert result.startswith("Installed skill 'evil'")
        root = mock_bot.data_path / "skills"
        assert (root / "evil" / "SKILL.md").is_file()
        assert not (tmp_path / "evil").exists()
        assert [
            p.name for p in root.iterdir() if not p.name.startswith(".")
        ] == ["evil"]
