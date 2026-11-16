"""EU legislation, through the EU Publications Office's SPARQL endpoint."""

import re

from ._parse import _json
from .errors import NotFoundError, SourceError
from .http import HttpClient


class EUPublications:
    """EU acts by CELEX number."""

    def __init__(self, get: HttpClient):
        self.get = get

    def act_title(self, celex: str) -> str:
        """The English title of an EU act, from the Publications Office.

        EUR-Lex itself answers scripts with a bot challenge; the Publications
        Office's SPARQL endpoint holds the same metadata.
        """
        if not re.fullmatch(r"[0-9A-Z()]+", celex):
            raise SourceError(f"not a CELEX number: {celex}")
        query = (
            "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> "
            "SELECT ?t WHERE { ?w cdm:resource_legal_id_celex "
            f'"{celex}"^^<http://www.w3.org/2001/XMLSchema#string> . '
            "?e cdm:expression_belongs_to_work ?w ; "
            "cdm:expression_uses_language <http://publications.europa.eu/"
            "resource/authority/language/ENG> ; cdm:expression_title ?t } "
            "LIMIT 1"
        )
        body = self.get(
            "https://publications.europa.eu/webapi/rdf/sparql",
            params={
                "query": query,
                "format": "application/sparql-results+json",
            },
        )
        rows = _json(body, "EU Publications Office")["results"]["bindings"]
        if not rows:
            raise NotFoundError(celex)
        return rows[0]["t"]["value"]
