-- Règles de validation d'une candidate : des DONNÉES, pas du code.
-- Une règle = un type d'évaluateur (liste fermée, dans validation.py) + ses
-- paramètres. Ajouter une règle d'un type existant = ajouter une ligne.

CREATE TABLE validation_rules (
    id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code     text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9_]{1,60}$'),
    kind     text NOT NULL,
    params   jsonb NOT NULL DEFAULT '{}',
    -- Raison affichée en clair quand la règle n'est pas satisfaite.
    message  text NOT NULL,
    enabled  boolean NOT NULL DEFAULT true,
    position integer NOT NULL DEFAULT 0
);

-- Jeu initial (ticket « règles de validation »). La liste des attributs obligatoires
-- n'est pas arrêtée : elle reste modifiable ici, sans toucher au code.
INSERT INTO validation_rules (code, kind, params, message, position) VALUES
    ('titre_requis', 'field_required', '{"field": "title"}',
     'Le titre est obligatoire pour publier une annonce.', 10),
    ('description_requise', 'field_required', '{"field": "description"}',
     'La description est obligatoire pour publier une annonce.', 20),
    ('prix_vente_requis', 'field_required', '{"field": "sale_price"}',
     'Le prix de vente est obligatoire.', 30),
    ('categorie_requise', 'field_required', '{"field": "category_id"}',
     'La candidate doit être classée dans une catégorie.', 40),
    ('au_moins_une_photo', 'min_photos', '{"min": 1}',
     'Une annonce a besoin d''au moins une photo.', 50),
    ('lien_taille_europe', 'link_size_and_europe', '{}',
     'Il faut au moins un lien d''achat qui propose la taille retenue et expédie depuis l''Europe.', 60);
