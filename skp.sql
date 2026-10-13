SELECT
    nip,
    skp_json,
    (
        SELECT GROUP_CONCAT(predikat_kinerja, ', ')
        FROM (
            SELECT
                json_extract(value, '$.predikat_kinerja') AS predikat_kinerja
            FROM json_each(pegawai.skp_json, '$.history_skp')
            WHERE
                (json_extract(value, '$.tahun') = 2026
                 AND json_extract(value, '$.periode') = 2)
                OR
                (json_extract(value, '$.tahun') = 2026
                 AND json_extract(value, '$.periode') = 1)
                OR
                (json_extract(value, '$.tahun') = 2025
                 AND json_extract(value, '$.periode') = 4)
            ORDER BY
                CASE
                    WHEN json_extract(value, '$.tahun') = 2026
                         AND json_extract(value, '$.periode') = 2 THEN 1
                    WHEN json_extract(value, '$.tahun') = 2026
                         AND json_extract(value, '$.periode') = 1 THEN 2
                    WHEN json_extract(value, '$.tahun') = 2025
                         AND json_extract(value, '$.periode') = 4 THEN 3
                END
        )
    ) AS predikat_kinerja
FROM pegawai;