-- Photos des candidates et photos mannequin de référence.
-- Les binaires vivent sur le volume ; la base ne porte qu'un chemin RELATIF à la
-- racine configurée, pour que remonter le volume ailleurs ne casse aucun affichage.

CREATE TABLE reference_photos (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    label      text NOT NULL,
    file_path  text NOT NULL UNIQUE CHECK (file_path NOT LIKE '/%' AND file_path NOT LIKE '%..%'),
    width      integer NOT NULL,
    height     integer NOT NULL,
    is_default boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Une seule référence par défaut, garantie par la base et non par le code.
CREATE UNIQUE INDEX reference_photos_one_default_idx ON reference_photos (is_default)
    WHERE is_default;

CREATE TABLE candidate_photos (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_id bigint NOT NULL REFERENCES candidates (id) ON DELETE CASCADE,
    -- « visuel » : déposé (visuel du site d'achat) ; « generee » : produite par la génération.
    kind         text NOT NULL DEFAULT 'visuel' CHECK (kind IN ('visuel', 'generee')),
    file_path    text NOT NULL UNIQUE CHECK (file_path NOT LIKE '/%' AND file_path NOT LIKE '%..%'),
    width        integer NOT NULL,
    height       integer NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX candidate_photos_candidate_id_idx ON candidate_photos (candidate_id);
