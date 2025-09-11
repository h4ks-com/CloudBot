"""Biological taxonomy plugin for CloudBot"""

from typing import Dict, List, Optional

import requests
from requests import RequestException

from cloudbot import hook


def is_valid_scientific_name(name: str) -> bool:
    words = name.split()
    if len(words) != 2:
        return False
    genus, species = words
    return len(genus) >= 3 and len(species) >= 3 and genus[0].isupper() and species.islower()


def search_gbif_vernacular(common_name: str) -> Optional[str]:
    """Search GBIF using vernacular (common) names"""
    headers = {"User-Agent": "CloudBot/1.0 (https://github.com/CloudBotIRC/CloudBot)"}

    # Try multiple search variations
    search_terms = [common_name.strip(), f"domestic {common_name.strip()}", f"{common_name.strip()} domestic"]

    for search_term in search_terms:
        try:
            response = requests.get(
                "https://api.gbif.org/v1/species/search",
                params={
                    "q": search_term,
                    "qField": "VERNACULAR",
                    "rank": "species",
                    "status": "ACCEPTED",
                    "datasetKey": "d7dddbf4-2cf0-4f39-9b2a-bb099caae36c",
                    "limit": 10,
                },
                headers=headers,
                timeout=5,
            )
            response.raise_for_status()

            for result in response.json().get("results", []):
                if not all(result.get(k) for k in ["kingdom", "phylum", "class"]):
                    continue

                # Skip obvious non-biological kingdoms like viruses for common names
                kingdom = result.get("kingdom")
                if kingdom in ["Viruses", "Bacteria", "Archaea"] and len(common_name.strip().split()) == 1:
                    continue

                # Check if the search term appears in vernacular names as exact match
                vernacular_names = result.get("vernacularNames", [])
                search_lower = common_name.lower().strip()

                for vern in vernacular_names:
                    vern_name = vern.get("vernacularName", "").lower().strip()
                    # Look for exact matches
                    if (
                        vern_name == search_lower
                        or vern_name == f"domestic {search_lower}"
                        or vern_name == f"{search_lower} domestic"
                        or search_lower == vern_name.replace("domestic ", "").strip()
                    ):
                        scientific_name = result.get("scientificName") or result.get("canonicalName")
                        if scientific_name:
                            # Extract just genus species (first two words) to remove author info
                            parts = scientific_name.split()
                            if len(parts) >= 2:
                                genus_species = f"{parts[0]} {parts[1]}"
                                if is_valid_scientific_name(genus_species):
                                    return genus_species

        except (RequestException, KeyError, AttributeError):
            continue

    return None


def get_related_species(genus: str, family: str) -> Dict[str, List[str]]:
    headers = {"User-Agent": "CloudBot/1.0 (https://github.com/CloudBotIRC/CloudBot)"}
    related: Dict[str, List[str]] = {"genus_siblings": [], "family_siblings": []}

    try:
        if genus:
            response = requests.get(
                "https://api.gbif.org/v1/species/search",
                params={"q": genus, "rank": "SPECIES", "limit": 4, "kingdom": "Animalia"},
                headers=headers,
                timeout=5,
            )
            response.raise_for_status()

            for result in response.json().get("results", []):
                name = result.get("scientificName", "")
                if name.startswith(genus + " ") and result.get("genus") == genus and len(related["genus_siblings"]) < 2:
                    related["genus_siblings"].append(name)

        if family:
            response = requests.get(
                "https://api.gbif.org/v1/species/search",
                params={"q": family, "rank": "GENUS", "limit": 4, "kingdom": "Animalia"},
                headers=headers,
                timeout=5,
            )
            response.raise_for_status()

            for result in response.json().get("results", []):
                name = result.get("scientificName", "")
                if (
                    result.get("family") == family
                    and name != genus
                    and result.get("rank") == "GENUS"
                    and len(related["family_siblings"]) < 2
                ):
                    related["family_siblings"].append(name)

    except RequestException:
        pass

    return related


def build_taxonomy_tree(taxonomy_data: Dict[str, Optional[str]], show_relatives: bool = True) -> List[str]:
    ranks = ["kingdom", "phylum", "class", "order", "family", "genus", "species"]
    hierarchy = [(rank, taxonomy_data[rank]) for rank in ranks if rank in taxonomy_data and taxonomy_data[rank]]

    if not hierarchy:
        return ["No taxonomic data available"]

    tree_lines = []
    for i, (rank, taxon) in enumerate(hierarchy):
        indent = "    " * (i - 1) + "└── " if i > 0 else ""
        tree_lines.append(indent + taxon)

        if show_relatives and i < len(hierarchy) - 1:
            related = get_related_species(taxonomy_data.get("genus"), taxonomy_data.get("family"))
            sibling_indent = "    " * i + "├── "

            if rank == "family":
                tree_lines.extend(sibling_indent + genus for genus in related["family_siblings"][:2])
            elif rank == "genus":
                tree_lines.extend(sibling_indent + species for species in related["genus_siblings"][:2])

    return tree_lines


def get_taxonomy_from_gbif(species_name: str) -> Optional[Dict[str, Optional[str]]]:
    headers = {"User-Agent": "CloudBot/1.0 (https://github.com/CloudBotIRC/CloudBot)"}

    try:
        response = requests.get(
            "https://api.gbif.org/v1/species/match", params={"name": species_name.strip()}, headers=headers, timeout=5
        )
        response.raise_for_status()
        data = response.json()

        if data.get("matchType") != "NONE" and data.get("scientificName"):
            taxonomy = {rank: data.get(rank) for rank in ["kingdom", "phylum", "class", "order", "family", "genus"]}
            taxonomy["species"] = data.get("scientificName")
            return taxonomy

        response = requests.get(
            "https://api.gbif.org/v1/species/search",
            params={"q": species_name.strip(), "limit": 10, "rank": "SPECIES"},
            headers=headers,
            timeout=5,
        )
        response.raise_for_status()

        for result in response.json().get("results", []):
            if not all(result.get(k) for k in ["kingdom", "phylum", "class"]):
                continue

            # Accept any kingdom that has proper taxonomic structure
            taxonomy = {rank: result.get(rank) for rank in ["kingdom", "phylum", "class", "order", "family", "genus"]}
            taxonomy["species"] = result.get("scientificName") or result.get("canonicalName")
            return taxonomy

    except RequestException:
        pass

    return None


@hook.command("taxonomy", "tax")
def taxonomy(text: str, reply) -> None:
    """<species> [-s|--simple] - retrieves taxonomic classification tree for <species>"""

    if not text or not text.strip():
        reply("Please provide a species name")
        return

    parts = text.strip().split()
    simple_mode = "--simple" in parts or "-s" in parts
    species_name = " ".join(p for p in parts if p not in ["--simple", "-s"])

    if not species_name:
        reply("Please provide a species name")
        return

    try:
        taxonomy_data = None

        # Try GBIF vernacular search first
        vernacular_name = search_gbif_vernacular(species_name)
        if vernacular_name:
            taxonomy_data = get_taxonomy_from_gbif(vernacular_name)

        # If vernacular search didn't work, try regular GBIF search
        if not taxonomy_data:
            taxonomy_data = get_taxonomy_from_gbif(species_name)

        if not taxonomy_data:
            reply(f"No taxonomic data found for '{species_name}'")
            return

        for line in build_taxonomy_tree(taxonomy_data, show_relatives=not simple_mode):
            reply(line)

    except Exception:
        reply(f"Error retrieving taxonomy data for '{species_name}'")
