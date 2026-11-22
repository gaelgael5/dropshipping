-- Annonces candidates, sites d'achat connus, et liens d'achat.

-- Sites d'achat : des données, pas du code. Le site d'un lien se déduit du domaine ;
-- un domaine absent de cette table laisse le site vide sans empêcher la capture.
CREATE TABLE purchase_sites (
    id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name   text NOT NULL,
    domain text NOT NULL UNIQUE CHECK (domain = lower(domain))
);

-- Jeu initial, modifiable sans code : ajouter un site = ajouter une ligne.
INSERT INTO purchase_sites (name, domain) VALUES
    ('AliExpress', 'aliexpress.com'),
    ('AliExpress', 'aliexpress.us'),
    ('Alibaba', 'alibaba.com'),
    ('Temu', 'temu.com'),
    ('Shein', 'shein.com'),
    ('Amazon', 'amazon.fr'),
    ('Amazon', 'amazon.com'),
    ('CJ Dropshipping', 'cjdropshipping.com');

CREATE TABLE candidates (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title      text,
    -- Champ simple, volontairement : la machine à états est repoussée (epic).
    status     text NOT NULL DEFAULT 'brouillon',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE purchase_links (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_id   bigint NOT NULL REFERENCES candidates (id) ON DELETE CASCADE,
    url            text NOT NULL,
    -- Clé de dédoublonnage (epic) : l'unicité est portée par la base, pas par une
    -- lecture préalable, pour que deux captures simultanées ne passent pas toutes deux.
    normalized_url text NOT NULL UNIQUE,
    site_id        bigint REFERENCES purchase_sites (id) ON DELETE SET NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX purchase_links_candidate_id_idx ON purchase_links (candidate_id);
