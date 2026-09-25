# How to contribute
The following guidelines for contribution should be followed if you want to
submit a pull request.

## Basic Overview
1. Read [Github documentation](http://help.github.com/) and [Pull Request documentation](http://help.github.com/send-pull-requests/)
2. Fork the repository
3. Create a new branch with a descriptive name for your feature
4. Edit the files, add new files
5. Add tests for your changes or new feature
6. [Run the checks] to make sure your changes follow the coding style
7. Add an entry in the [CHANGELOG]
8. Commit changes, push to your fork on GitHub
9. Create a new pull request, provide a short summary of changes in the title line, with more information in the description field.

## Run the checks
The checks run as git hooks with [prek]: ruff lints and formats, ty type checks.
1. Run `uv sync` to install the tools
2. Run `uv run prek install` to run the checks on each `git commit`
3. Run `uv run prek run --all-files` to check everything by hand

## Submit Changes
1. Push your changes to a topic branch in your fork of the repository.
2. Open a pull request to the original repository and choose the `main` branch.
3. Correct any issues shown by the automated checks
4. Join the [IRC channel] if you have any questions or concerns, or if you just want to talk with other devs

# Additional Resources
* [General GitHub documentation](http://help.github.com/)
* [GitHub pull request documentation](http://help.github.com/send-pull-requests/)
* [Read the Issue Guidelines by @necolas](https://github.com/necolas/issue-guidelines/blob/master/CONTRIBUTING.md) for more details
* [This CONTRIBUTING.md from here](https://github.com/anselmh/CONTRIBUTING.md)

[prek]: https://github.com/j178/prek
[Run the checks]: #run-the-checks
[CHANGELOG]: CHANGELOG.md
[IRC channel]: README.md#support
