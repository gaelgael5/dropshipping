"""Attributs éditables champ par champ : liste fermée, validation, affichage.

La liste est FERMÉE (ticket « fiche ») : ajouter un attribut de vente se décide,
il ne s'improvise pas. Elle sert aussi de liste blanche des colonnes SQL.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

MAX_PRICE = Decimal("99999999.99")


class FieldValueError(ValueError):
    """Valeur refusée ; le message est affiché tel quel à côté du champ."""


@dataclass(frozen=True)
class FieldSpec:
    column: str
    label: str
    widget: str  # text | textarea | price | currency | tristate | list
    parse: Callable[[str], object]
    display: Callable[[object], str]


def _text(max_len: int, required: bool = False) -> Callable[[str], object]:
    def parse(raw: str) -> object:
        value = raw.strip()
        if not value:
            if required:
                raise FieldValueError("Ce champ ne peut pas être vide.")
            # Vide = vide : aucune valeur inventée ni déduite (ticket).
            return None
        if len(value) > max_len:
            raise FieldValueError(f"{max_len} caractères maximum.")
        return value

    return parse


def parse_price(raw: str) -> object:
    value = raw.strip().replace(" ", "").replace(",", ".").removesuffix("€")
    if not value:
        return None
    try:
        price = Decimal(value)
    except InvalidOperation:
        raise FieldValueError("Prix invalide : un nombre est attendu, ex. 12,50.") from None
    if not price.is_finite() or price < 0:
        raise FieldValueError("Le prix ne peut pas être négatif.")
    if price > MAX_PRICE or price != price.quantize(Decimal("0.01")):
        raise FieldValueError("Prix invalide : deux décimales au plus.")
    return price


def parse_currency(raw: str) -> object:
    value = raw.strip().upper()
    if not value:
        return None
    if not re.fullmatch(r"[A-Z]{3}", value):
        raise FieldValueError("Devise attendue en code ISO à 3 lettres, ex. EUR, USD.")
    return value


_TRISTATE = {"": None, "inconnu": None, "oui": True, "non": False}


def parse_tristate(raw: str) -> object:
    key = raw.strip().lower()
    if key not in _TRISTATE:
        raise FieldValueError("Valeur attendue : oui, non ou inconnu.")
    return _TRISTATE[key]


def parse_sizes(raw: str) -> object:
    # Plusieurs valeurs ou aucune ; dédoublonnées en gardant l'ordre saisi.
    sizes = [s.strip() for s in re.split(r"[,;\n]", raw) if s.strip()]
    if any(len(s) > 20 for s in sizes) or len(sizes) > 50:
        raise FieldValueError("Tailles trop longues ou trop nombreuses.")
    return list(dict.fromkeys(sizes))


def _show_text(value: object) -> str:
    return "" if value is None else str(value)


def _show_price(value: object) -> str:
    return "" if value is None else f"{value:.2f}".replace(".", ",")


def _show_tristate(value: object) -> str:
    if value is None:
        return "inconnu"
    return "oui" if value else "non"


def _show_list(value: object) -> str:
    return ", ".join(value) if isinstance(value, list) else ""


CANDIDATE_FIELDS: dict[str, FieldSpec] = {
    "title": FieldSpec("title", "Titre", "text", _text(200), _show_text),
    "description": FieldSpec("description", "Description", "textarea", _text(5000), _show_text),
    "brand": FieldSpec("brand", "Marque", "text", _text(100), _show_text),
    "size": FieldSpec("size", "Taille", "text", _text(50), _show_text),
    "sale_price": FieldSpec("sale_price", "Prix de vente (€)", "price", parse_price, _show_price),
    "status": FieldSpec("status", "Statut", "text", _text(40, required=True), _show_text),
}

LINK_FIELDS: dict[str, FieldSpec] = {
    "purchase_price": FieldSpec(
        "purchase_price", "Prix d'achat", "price", parse_price, _show_price
    ),
    "currency": FieldSpec("currency", "Devise", "currency", parse_currency, _show_text),
    "ships_from_europe": FieldSpec(
        "ships_from_europe",
        "Expédition depuis l'Europe",
        "tristate",
        parse_tristate,
        _show_tristate,
    ),
    "available_sizes": FieldSpec(
        "available_sizes", "Tailles disponibles", "list", parse_sizes, _show_list
    ),
}
