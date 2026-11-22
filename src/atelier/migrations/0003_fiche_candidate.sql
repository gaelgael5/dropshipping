-- Attributs de vente d'une candidate, et attributs de chaque lien d'achat.
-- Tous facultatifs : la fiche se remplit en plusieurs passes, un champ vide reste NULL.

ALTER TABLE candidates
    ADD COLUMN description text,
    ADD COLUMN brand       text,
    ADD COLUMN size        text,
    ADD COLUMN sale_price  numeric(10, 2) CHECK (sale_price >= 0);

ALTER TABLE purchase_links
    ADD COLUMN purchase_price    numeric(10, 2) CHECK (purchase_price >= 0),
    ADD COLUMN currency          char(3) CHECK (currency ~ '^[A-Z]{3}$'),
    -- NULL = on ne sait pas encore ; distinct de « non ».
    ADD COLUMN ships_from_europe boolean,
    ADD COLUMN available_sizes   text[] NOT NULL DEFAULT '{}',
    ADD COLUMN updated_at        timestamptz NOT NULL DEFAULT now();
