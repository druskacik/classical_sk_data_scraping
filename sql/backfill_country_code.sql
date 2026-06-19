-- Backfill current scraper data after applying the nullable country_code migration.
-- Run manually, then inspect the unresolved rows returned at the end.

UPDATE classical_concert
SET country_code = 'CZ'
WHERE country_code IS NULL
  AND (
    source_url IN (
      'https://www.auditeorganum.cz',
      'https://www.berg.cz',
      'https://www.ceskafilharmonie.cz',
      'https://www.hamu.cz',
      'https://www.neoklasikorchestr.cz',
      'https://www.prgphil.cz',
      'https://www.varhannifestival.cz'
    )
    OR source IN (
      'Audite Organum',
      'Berg Orchestra',
      'Česká filharmonie',
      'HAMU',
      'NeoKlasik orchestr',
      'Prague Philharmonia',
      'Varhanní festival'
    )
  );

UPDATE potential_event
SET country_code = 'CZ'
WHERE country_code IS NULL
  AND (
    source_url IN (
      'https://www.auditeorganum.cz',
      'https://www.berg.cz',
      'https://www.ceskafilharmonie.cz',
      'https://www.hamu.cz',
      'https://www.neoklasikorchestr.cz',
      'https://www.prgphil.cz',
      'https://www.varhannifestival.cz'
    )
    OR source IN (
      'Audite Organum',
      'Berg Orchestra',
      'Česká filharmonie',
      'HAMU',
      'NeoKlasik orchestr',
      'Prague Philharmonia',
      'Varhanní festival'
    )
  );

UPDATE classical_concert
SET country_code = 'SK'
WHERE country_code IS NULL
  AND (
    source_url IN (
      'http://www.filharmonia.sk',
      'https://devin.stvr.sk',
      'https://goout.net',
      'https://kultura.trnava.sk',
      'https://nedbalka.sk',
      'https://podujatia.pkopresov.sk/',
      'https://predpredaj.zoznam.sk/',
      'https://simachart.weebly.com',
      'https://snd.sk',
      'https://skozilina.sk',
      'https://tootoot.fm',
      'https://www.cultusruzinov.sk',
      'https://www.konvergencie.sk',
      'https://www.kpvh.sk',
      'https://www.sdke.sk',
      'https://www.sfk.sk',
      'https://www.stateopera.sk',
      'https://www.vivamusica.sk'
    )
    OR source_url LIKE '%.sk%'
  );

UPDATE potential_event
SET country_code = 'SK'
WHERE country_code IS NULL
  AND (
    source_url IN (
      'http://www.filharmonia.sk',
      'https://devin.stvr.sk',
      'https://goout.net',
      'https://kultura.trnava.sk',
      'https://nedbalka.sk',
      'https://podujatia.pkopresov.sk/',
      'https://predpredaj.zoznam.sk/',
      'https://simachart.weebly.com',
      'https://snd.sk',
      'https://skozilina.sk',
      'https://tootoot.fm',
      'https://www.cultusruzinov.sk',
      'https://www.konvergencie.sk',
      'https://www.kpvh.sk',
      'https://www.sdke.sk',
      'https://www.sfk.sk',
      'https://www.stateopera.sk',
      'https://www.vivamusica.sk'
    )
    OR source_url LIKE '%.sk%'
  );

SELECT 'classical_concert' AS table_name, id, title, date, source, source_url, city
FROM classical_concert
WHERE country_code IS NULL
UNION ALL
SELECT 'potential_event' AS table_name, id, title, date, source, source_url, city
FROM potential_event
WHERE country_code IS NULL
ORDER BY table_name, id;
