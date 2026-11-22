-- Arbre de catégories de l'atelier, ses valeurs par défaut, et la correspondance
-- vers les taxonomies des plateformes de vente.

-- Liste d'adjacence : l'arbre reste petit (quelques centaines de nœuds) et se
-- déplace souvent ; un chemin matérialisé imposerait de réécrire tout un
-- sous-arbre à chaque déplacement.
CREATE TABLE categories (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- RESTRICT : supprimer une catégorie qui a des enfants est refusé, jamais en cascade.
    parent_id             bigint REFERENCES categories (id) ON DELETE RESTRICT,
    label                 text NOT NULL,
    position              integer NOT NULL DEFAULT 0,
    -- Les trois valeurs par défaut ; NULL = héritée du plus proche ancêtre.
    photo_prompt_template text,
    description_template  text,
    parcel_size           text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (parent_id IS NULL OR parent_id <> id)
);

CREATE INDEX categories_parent_id_idx ON categories (parent_id);

-- Clé interne de l'arbre = identifiant de l'atelier ; l'identifiant d'une plateforme
-- n'est qu'une valeur de correspondance (les plateformes changent leur taxonomie).
CREATE TABLE category_platform_mappings (
    category_id          bigint NOT NULL REFERENCES categories (id) ON DELETE CASCADE,
    platform             text NOT NULL CHECK (platform ~ '^[a-z0-9][a-z0-9_-]{0,39}$'),
    platform_category_id text NOT NULL CHECK (length(platform_category_id) BETWEEN 1 AND 100),
    PRIMARY KEY (category_id, platform)
);

ALTER TABLE candidates
    ADD COLUMN category_id  bigint REFERENCES categories (id) ON DELETE RESTRICT,
    -- Champs que la catégorie peut préremplir (en plus de la description).
    ADD COLUMN photo_prompt text,
    ADD COLUMN parcel_size  text;

CREATE INDEX candidates_category_id_idx ON candidates (category_id);
