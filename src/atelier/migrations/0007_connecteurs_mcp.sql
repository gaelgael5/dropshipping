-- Connexions des clients MCP : clefs d'API des agents, et OAuth 2.1 pour Claude web.
-- Aucun jeton n'est stocké en clair : seule son empreinte SHA-256 l'est. Les jetons
-- sont aléatoires (256 bits), une empreinte rapide suffit donc ; un hachage lent
-- (bcrypt…) ne protège que des secrets devinables, comme un mot de passe.

CREATE TABLE mcp_api_keys (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
    -- Début de la clef, affiché pour la reconnaître ; trop court pour aider à la deviner.
    prefix       text NOT NULL,
    key_hash     bytea NOT NULL UNIQUE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revoked_at   timestamptz
);

-- Clients enregistrés dynamiquement (RFC 7591) : claude.ai s'inscrit lui-même.
CREATE TABLE oauth_clients (
    client_id     text PRIMARY KEY,
    client_name   text NOT NULL,
    redirect_uris text[] NOT NULL CHECK (cardinality(redirect_uris) BETWEEN 1 AND 10),
    created_at    timestamptz NOT NULL DEFAULT now(),
    revoked_at    timestamptz
);

-- Code à usage unique affiché sur la page Connecteurs : la preuve, au consentement,
-- que la personne qui autorise a accès à l'atelier.
CREATE TABLE oauth_pairing_codes (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code_hash  bytea NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    used_at    timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Compteur global d'échecs de saisie du code d'appairage (une seule ligne).
CREATE TABLE oauth_pairing_failures (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    count     integer NOT NULL DEFAULT 0
);
INSERT INTO oauth_pairing_failures DEFAULT VALUES;

CREATE TABLE oauth_auth_codes (
    code_hash      bytea PRIMARY KEY,
    client_id      text NOT NULL REFERENCES oauth_clients (client_id) ON DELETE CASCADE,
    redirect_uri   text NOT NULL,
    code_challenge text NOT NULL,
    expires_at     timestamptz NOT NULL,
    used_at        timestamptz
);

CREATE TABLE oauth_tokens (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    token_hash   bytea NOT NULL UNIQUE,
    kind         text NOT NULL CHECK (kind IN ('access', 'refresh')),
    client_id    text NOT NULL REFERENCES oauth_clients (client_id) ON DELETE CASCADE,
    expires_at   timestamptz NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revoked_at   timestamptz
);

CREATE INDEX oauth_tokens_client_idx ON oauth_tokens (client_id);
