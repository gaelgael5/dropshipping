-- Les fournisseurs deviennent des configurations : plusieurs par type (deux comptes
-- OpenAI, par exemple), ajoutées et supprimées depuis l'écran. Les TYPES restent fermés
-- (openai, gemini) : un nouveau type demande un client écrit dans le code.
ALTER TABLE ai_providers DROP CONSTRAINT ai_providers_name_check;
ALTER TABLE ai_providers ADD COLUMN kind text;
UPDATE ai_providers SET kind = name;
ALTER TABLE ai_providers
    ALTER COLUMN kind SET NOT NULL,
    ADD CONSTRAINT ai_providers_kind_check CHECK (kind IN ('openai', 'gemini')),
    -- Identifiant stable par lequel le code désignera la configuration (principal, repli).
    ADD CONSTRAINT ai_providers_name_format CHECK (name ~ '^[a-z][a-z0-9_]{0,39}$'),
    ADD CONSTRAINT ai_providers_label_check CHECK (length(btrim(label)) BETWEEN 1 AND 100),
    -- Dernier test de connexion, pour voir l'état d'un coup d'œil dans la liste.
    ADD COLUMN last_check_at timestamptz,
    ADD COLUMN last_check_ok boolean;
