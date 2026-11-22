"""Normalisation des URL d'achat, clé de dédoublonnage des liens."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_URL_LENGTH = 2048
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

# Paramètres de suivi publicitaire ou de session : ils changent d'un partage à
# l'autre sans désigner un autre article. Liste volontairement prudente — retirer
# un paramètre qui identifie l'article (variante, couleur) fusionnerait deux liens
# distincts, ce qui est pire qu'un doublon.
_TRACKING_PARAMS = frozenset(
    {
        "gclid",
        "gclsrc",
        "dclid",
        "fbclid",
        "msclkid",
        "yclid",
        "igshid",
        "ttclid",
        "mc_cid",
        "mc_eid",
        "_ga",
        "_gl",
        "ref",
        "ref_",
        "tag",
        "linkcode",
        "spm",
        "scm",
        "algo_pvid",
        "algo_exp_id",
        "pdp_npi",
        "pdp_ext_f",
        "gatewayadapt",
        "srcsns",
        "businesstype",
        "sharer_id",
        "share_id",
        "_t",
        "_x_sessn_id",
        "refer_page_name",
        "refer_page_id",
        "refer_source",
        "_x_ads_channel",
        "_x_ads_account",
        "_x_vst_scene",
        "_x_ns_prz_type",
        "_x_cid",
        "_x_sid",
        "sourcetype",
        "aff_fcid",
        "aff_fsk",
        "aff_platform",
        "aff_trace_key",
        "terminal_id",
        "afsmartredirect",
        "sk",
        "pd_rd_r",
        "pd_rd_w",
        "pd_rd_wg",
        "pd_rd_i",
        "pf_rd_p",
        "pf_rd_r",
        "psc",
        "th",
        "url_from",
        "share_token",
    }
)
_TRACKING_PREFIXES = ("utm_", "aff_", "pd_rd_", "pf_rd_", "_x_")


class InvalidUrlError(ValueError):
    """La chaîne reçue n'est pas une URL d'article exploitable."""


@dataclass(frozen=True)
class NormalizedUrl:
    original: str
    normalized: str
    host: str


def normalize_purchase_url(raw: str) -> NormalizedUrl:
    text = raw.strip()
    if not text or len(text) > MAX_URL_LENGTH:
        raise InvalidUrlError("Ce n'est pas une URL : la valeur est vide ou trop longue.")
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        raise InvalidUrlError("Ce n'est pas une URL : elle doit commencer par http:// ou https://.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not _HOST_RE.match(host):
        raise InvalidUrlError("Ce n'est pas une URL : le nom de domaine est absent ou invalide.")
    # https et sans « www. » : le même article partagé en http ou depuis www
    # doit retomber sur la même clé.
    key_host = host.removeprefix("www.")
    query = urlencode(sorted(_kept_query_params(parts.query)))
    # Fragment retiré : il n'est jamais envoyé au site, il ne désigne pas l'article.
    normalized = urlunsplit(("https", key_host, parts.path or "/", query, ""))
    return NormalizedUrl(original=text, normalized=normalized, host=host)


def _kept_query_params(query: str) -> list[tuple[str, str]]:
    return [
        (name, value)
        for name, value in parse_qsl(query, keep_blank_values=True)
        if not _is_tracking_param(name)
    ]


def _is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered in _TRACKING_PARAMS or lowered.startswith(_TRACKING_PREFIXES)


def candidate_domains(host: str) -> list[str]:
    """« fr.aliexpress.com » → ["fr.aliexpress.com", "aliexpress.com"] (sans le TLD seul)."""
    labels = host.split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)]
