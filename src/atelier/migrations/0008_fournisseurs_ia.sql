-- Réglages des fournisseurs d'IA (Gemini, OpenAI) : modèles utilisés et référence de
-- la clef dans le coffre. La clef elle-même n'est jamais ici, seulement sa référence.
CREATE TABLE ai_providers (
    name        text PRIMARY KEY CHECK (name IN ('openai', 'gemini')),
    label       text NOT NULL,
    secret_ref  text NOT NULL CHECK (secret_ref ~ '^[a-z][a-z0-9_]{0,39}/[a-z][a-z0-9_]{0,63}$'),
    text_model  text NOT NULL CHECK (text_model ~ '^[A-Za-z0-9._-]{1,100}$'),
    image_model text NOT NULL CHECK (image_model ~ '^[A-Za-z0-9._-]{1,100}$'),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Modèles par défaut à la date de livraison ; réglables sur la page Fournisseurs, car
-- les noms changent plus vite que le code.
INSERT INTO ai_providers (name, label, secret_ref, text_model, image_model) VALUES
    ('gemini', 'Google Gemini', 'ia/gemini_api_key', 'gemini-3.6-flash', 'gemini-nano-banana-2.1'),
    ('openai', 'OpenAI (ChatGPT)', 'ia/openai_api_key', 'gpt-5-mini', 'gpt-image-1.5');
