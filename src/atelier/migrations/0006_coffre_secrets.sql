-- Coffre de secrets de l'atelier (clefs d'API des fournisseurs, jetons de plateformes).
-- Les valeurs ne sont JAMAIS stockées en clair : `ciphertext` est un AES-GCM produit par
-- l'application avec une clef maîtresse hors base (ATELIER_SECRETS_KEY).

CREATE TABLE secret_categories (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Le slug entre dans la référence « slug/nom » lue par le code : il est figé à la
    -- création (seul le libellé se renomme), sinon renommer casserait les appels.
    slug        text NOT NULL UNIQUE CHECK (slug ~ '^[a-z][a-z0-9_]{0,39}$'),
    label       text NOT NULL CHECK (length(btrim(label)) BETWEEN 1 AND 100),
    description text,
    position    integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE secrets (
    -- Identifiant fourni par l'application (nextval) : il sert de donnée authentifiée
    -- du chiffrement, il doit donc être connu AVANT l'insertion.
    id           bigint PRIMARY KEY,
    -- RESTRICT : une catégorie non vide ne se supprime pas, jamais en cascade.
    category_id  bigint NOT NULL REFERENCES secret_categories (id) ON DELETE RESTRICT,
    name         text NOT NULL CHECK (name ~ '^[a-z][a-z0-9_]{0,63}$'),
    description  text,
    ciphertext   bytea NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    UNIQUE (category_id, name)
);

CREATE SEQUENCE secrets_id_seq OWNED BY secrets.id;

INSERT INTO secret_categories (slug, label, description, position) VALUES
    ('ia', 'Fournisseurs d''IA', 'Clefs des API de génération d''image et de texte (Gemini, OpenAI…).', 10),
    ('plateformes', 'Plateformes de vente', 'Jetons des plateformes où les annonces seront publiées.', 20),
    ('divers', 'Divers', 'Ce qui ne rentre dans aucune autre catégorie.', 30);
