-- Seed: 002_seed_data.sql
-- Created: 2026-09-09
-- Description: Seed data for development and testing
--
-- To apply: psql -d secyt -f seeds/002_seed_data.sql

BEGIN;

INSERT INTO schema_migrations (filename) VALUES ('002_seed_data.sql');

-- ============================================================
-- ACTOR TYPES (the 6 from the PIDTA framework)
-- ============================================================
INSERT INTO actor_types (id, name, description) VALUES
(1, 'Academia',      'Universidades, centros de investigación, institutos CONICET'),
(2, 'Startup',       'Empresas de base tecnológica / deep tech en etapa temprana'),
(3, 'Corporation',   'Empresas establecidas con actividad en deep tech'),
(4, 'Government',    'Organismos gubernamentales de CyT, ministerios, agencias'),
(5, 'Enabler',       'Incubadoras, aceleradoras, parques tecnológicos, UVTs'),
(6, 'Fund',          'Fondos de inversión, capital ángel, venture capital');

-- ============================================================
-- TECHNOLOGICAL AREAS (7 verticals + sub-areas)
-- ============================================================

-- Level 0: Verticals
INSERT INTO technological_areas (id, name, description, parent_id) VALUES
(1,  'Biotechnology',                    'Biotecnología aplicada',                        NULL),
(2,  'Artificial Intelligence',          'Inteligencia artificial y machine learning',     NULL),
(3,  'Nanotechnology',                   'Nanotecnología y nanomateriales',               NULL),
(4,  'Advanced Materials',               'Materiales avanzados y compuestos',              NULL),
(5,  'Photonics',                        'Fotónica, óptica y láseres',                    NULL),
(6,  'Electronics & Semiconductors',     'Electrónica, semiconductores, IoT',             NULL),
(7,  'Quantum Technologies',             'Computación y comunicaciones cuánticas',         NULL);

-- Level 1: Sub-areas
INSERT INTO technological_areas (id, name, description, parent_id) VALUES
-- Biotechnology sub-areas
(10, 'Industrial Biotechnology',         'Bioprocesos, enzimas industriales',              1),
(11, 'Medical Biotechnology',            'Biofármacos, diagnóstico molecular',             1),
(12, 'Agricultural Biotechnology',       'Edición genética, biocontrol',                   1),
-- AI sub-areas
(20, 'Computer Vision',                  'Visión por computadora, procesamiento de imagen', 2),
(21, 'Natural Language Processing',      'Procesamiento de lenguaje natural',              2),
(22, 'Robotics & Autonomous Systems',    'Robótica, drones, sistemas autónomos',           2),
-- Nanotechnology sub-areas
(30, 'Nanosensors',                      'Sensores a nanoescala',                          3),
(31, 'Nanomedicine',                     'Nanopartículas para drug delivery',              3),
-- Advanced Materials sub-areas
(40, 'Composites',                       'Materiales compuestos de alto rendimiento',      4),
(41, 'Biomaterials',                     'Materiales biocompatibles e implantables',        4),
-- Electronics sub-areas
(60, 'IoT & Embedded Systems',           'Internet de las cosas, sistemas embebidos',      6),
(61, 'Power Electronics',                'Electrónica de potencia, conversores',            6);

-- ============================================================
-- LOCATIONS
-- ============================================================
INSERT INTO locations (id, country, province, municipality, address, latitude, longitude) VALUES
(1,  'Argentina', 'Córdoba', 'Córdoba Capital',    'Av. Haya de la Torre s/n, Ciudad Universitaria',  -31.439800, -64.189500),
(2,  'Argentina', 'Córdoba', 'Córdoba Capital',    'Av. Vélez Sarsfield 1611',                        -31.424500, -64.190200),
(3,  'Argentina', 'Córdoba', 'Córdoba Capital',    'Av. Colón 5000',                                  -31.406700, -64.218900),
(4,  'Argentina', 'Córdoba', 'Córdoba Capital',    'Duarte Quirós 96',                                -31.419500, -64.190100),
(5,  'Argentina', 'Córdoba', 'Córdoba Capital',    'Av. Richieri 2468, Parque Industrial',            -31.383200, -64.241300),
(6,  'Argentina', 'Córdoba', 'Córdoba Capital',    'Naciones Unidas 575',                             -31.423100, -64.199800),
(7,  'Argentina', 'Córdoba', 'Río Cuarto',         'Ruta 36, Km 601',                                -33.130300, -64.349800),
(8,  'Argentina', 'Córdoba', 'Villa María',        'Av. Jauretche 1555',                              -32.408500, -63.240200),
(9,  'Argentina', 'Buenos Aires', 'CABA',          'Godoy Cruz 2390, Palermo',                        -34.586300, -58.410800),
(10, 'Argentina', 'Córdoba', 'Córdoba Capital',    'Poeta Lugones 210',                               -31.412500, -64.208100),
(11, 'Argentina', 'Córdoba', 'Córdoba Capital',    'Ambrosio Olmos 820',                              -31.416800, -64.193700),
(12, 'Argentina', 'Córdoba', 'Córdoba Capital',    'Bv. Chacabuco 1300',                              -31.420000, -64.184500);

-- ============================================================
-- SOURCES
-- ============================================================
INSERT INTO sources (id, title, url, source_type, publication_date, notes, verified, verified_by) VALUES
(1,  'Registro CONICET - Institutos',           'https://www.conicet.gov.ar/institutos/',                 'official_registry',  '2026-01-15', 'Base de institutos CONICET por provincia',            TRUE,  'Investigador 1'),
(2,  'Crunchbase - Startups Córdoba',            'https://www.crunchbase.com/',                            'portfolio',          '2026-03-01', 'Búsqueda filtrada por Córdoba, Deep Tech',            FALSE, NULL),
(3,  'ADEC - Directorio de Empresas',            'https://www.adec.org.ar/',                               'chamber',            '2026-02-10', 'Agencia para el Desarrollo Económico de Córdoba',     TRUE,  'Investigador 2'),
(4,  'Ministerio de Ciencia y Tecnología CBA',   'https://mincyt.cba.gov.ar/',                             'official_registry',  '2026-06-01', 'Portal del ministerio provincial de CyT',             TRUE,  'Investigador 1'),
(5,  'Mapa Biotech Argentina',                   'https://www.argentina.gob.ar/',                          'prior_mapping',      '2025-11-20', 'Relevamiento previo de empresas biotech',             TRUE,  'Investigador 2'),
(6,  'LinkedIn - Perfiles corporativos',          'https://www.linkedin.com/',                              'press',              '2026-08-01', 'Extracción semi-automatizada de perfiles',            FALSE, NULL),
(7,  'Endeavor Argentina - Scale-ups',            'https://www.endeavor.org.ar/',                           'ranking',            '2026-04-15', 'Ranking y portafolio de emprendimientos Endeavor',    TRUE,  'Investigador 1');

-- ============================================================
-- ACTORS
-- ============================================================

-- === ACADEMIA (ids 1-5) ===
INSERT INTO actors (id, name, description, website, email, phone, year_created, metadata) VALUES
(1,  'Universidad Nacional de Córdoba',
     'Principal universidad pública de Córdoba, con 13 facultades y múltiples centros de investigación en deep tech.',
     'https://www.unc.edu.ar', 'info@unc.edu.ar', '+54 351 433-2086', 1613,
     '{"acronym": "UNC", "faculties": 13, "researchers": 4500}'::jsonb),

(2,  'CONICET CCT Córdoba',
     'Centro Científico Tecnológico del CONICET en Córdoba, articula más de 20 institutos de investigación.',
     'https://cct.conicet.gov.ar/cordoba/', 'cct-cordoba@conicet.gov.ar', '+54 351 468-2616', 1958,
     '{"acronym": "CCT-CBA", "institutes_count": 22}'::jsonb),

(3,  'Universidad Tecnológica Nacional - FRC',
     'Facultad Regional Córdoba de la UTN, orientada a ingeniería y transferencia tecnológica.',
     'https://www.frc.utn.edu.ar', 'info@frc.utn.edu.ar', '+54 351 598-6000', 1959,
     '{"acronym": "UTN-FRC", "focus": "engineering"}'::jsonb),

(4,  'Universidad Nacional de Río Cuarto',
     'Universidad pública con investigación en biotecnología agrícola y materiales.',
     'https://www.unrc.edu.ar', 'info@unrc.edu.ar', '+54 358 467-6200', 1971,
     '{"acronym": "UNRC"}'::jsonb),

(5,  'Universidad Nacional de Villa María',
     'Universidad con programas de vinculación tecnológica y desarrollo regional.',
     'https://www.unvm.edu.ar', 'info@unvm.edu.ar', '+54 353 453-9100', 1995,
     '{"acronym": "UNVM"}'::jsonb);

-- === STARTUPS (ids 6-12) ===
INSERT INTO actors (id, name, description, website, email, phone, year_created, metadata) VALUES
(6,  'BioSeed Genomics',
     'Startup de edición genética para mejora de cultivos resistentes a sequía.',
     'https://www.bioseedgenomics.com.ar', 'info@bioseedgenomics.com.ar', NULL, 2021,
     '{"trl": "4-6", "business_stage": "seed", "business_model": "B2B", "employees": 12, "exports": false, "founders_gender_diversity": true}'::jsonb),

(7,  'NanoSense Tech',
     'Desarrollo de nanosensores para detección temprana de contaminantes en agua.',
     'https://www.nanosensetech.com', 'hello@nanosensetech.com', NULL, 2022,
     '{"trl": "1-3", "business_stage": "pre-seed", "business_model": "B2B", "employees": 5, "exports": false, "founders_gender_diversity": false}'::jsonb),

(8,  'Visión AI',
     'Plataforma de computer vision para control de calidad industrial en líneas de producción.',
     'https://www.visionai.com.ar', 'contacto@visionai.com.ar', '+54 351 600-1234', 2019,
     '{"trl": "7-9", "business_stage": "series_a", "business_model": "B2B", "employees": 35, "exports": true, "founders_gender_diversity": false}'::jsonb),

(9,  'FotoMed',
     'Dispositivos fotónicos portátiles para diagnóstico dermatológico.',
     'https://www.fotomed.com.ar', 'info@fotomed.com.ar', NULL, 2023,
     '{"trl": "1-3", "business_stage": "pre-seed", "business_model": "B2B2C", "employees": 4, "exports": false, "founders_gender_diversity": true}'::jsonb),

(10, 'QBit Security',
     'Protocolos de comunicación cuántica para ciberseguridad bancaria.',
     'https://www.qbitsecurity.io', 'info@qbitsecurity.io', NULL, 2024,
     '{"trl": "1-3", "business_stage": "pre-seed", "business_model": "B2B", "employees": 3, "exports": false, "founders_gender_diversity": false}'::jsonb),

(11, 'AgriDrone CBA',
     'Drones autónomos con IA para agricultura de precisión y monitoreo de cultivos.',
     'https://www.agridronecba.com', 'ventas@agridronecba.com', '+54 351 700-5678', 2020,
     '{"trl": "7-9", "business_stage": "seed", "business_model": "B2B", "employees": 18, "exports": true, "founders_gender_diversity": true}'::jsonb),

(12, 'CompositAR',
     'Fabricación de materiales compuestos livianos para industria aeroespacial y automotriz.',
     'https://www.compositar.com.ar', 'contacto@compositar.com.ar', NULL, 2018,
     '{"trl": "7-9", "business_stage": "series_a", "business_model": "B2B", "employees": 28, "exports": true, "founders_gender_diversity": false}'::jsonb);

-- === CORPORATIONS (ids 13-16) ===
INSERT INTO actors (id, name, description, website, email, phone, year_created, metadata) VALUES
(13, 'Grupo Arcor',
     'Multinacional argentina de alimentos. Invierte en biotecnología industrial y procesos sustentables.',
     'https://www.arcor.com', 'rse@arcor.com', '+54 351 420-1000', 1951,
     '{"has_rnd": true, "invests_in_startups": true, "infrastructure_available": true}'::jsonb),

(14, 'Stamatti Constructora',
     'Empresa cordobesa de construcción que utiliza materiales avanzados y BIM con IA.',
     'https://www.stamatti.com', 'info@stamatti.com', '+54 351 488-7000', 1970,
     '{"has_rnd": false, "invests_in_startups": false, "infrastructure_available": true}'::jsonb),

(15, 'Corporación Espacial Argentina (CEA)',
     'Empresa ficticia de integración satelital. Opera con CONAE y laboratorios nacionales.',
     'https://www.cea.com.ar', 'info@cea.com.ar', '+54 351 490-2000', 2005,
     '{"has_rnd": true, "invests_in_startups": true, "infrastructure_available": true}'::jsonb),

(16, 'Bioceres Crop Solutions',
     'Empresa agbiotech cotizada. Lidera en edición genética de cultivos y bioinsumos.',
     'https://www.biocerescropsolutions.com', 'info@bioceres.com', '+54 341 461-1000', 2001,
     '{"has_rnd": true, "invests_in_startups": true, "infrastructure_available": true}'::jsonb);

-- === GOVERNMENT (ids 17-19) ===
INSERT INTO actors (id, name, description, website, email, phone, year_created, metadata) VALUES
(17, 'Ministerio de Ciencia y Tecnología de Córdoba',
     'Organismo provincial responsable de las políticas públicas de CyT.',
     'https://mincyt.cba.gov.ar', 'contacto@mincyt.cba.gov.ar', '+54 351 434-3303', 2007,
     '{"type": "ministry", "programs": ["FONTEC", "Córdoba Innova"]}'::jsonb),

(18, 'Agencia Nacional de Promoción de la Investigación (Agencia I+D+i)',
     'Agencia nacional que financia proyectos de investigación y desarrollo tecnológico.',
     'https://www.argentina.gob.ar/ciencia/agencia', 'info@agencia.gob.ar', '+54 11 4899-5000', 1996,
     '{"type": "agency", "instruments": ["FONTAR", "FONCYT", "FONARSEC", "FONSOFT"]}'::jsonb),

(19, 'Municipalidad de Córdoba - Sec. de Economía y Empleo',
     'Área municipal de promoción del ecosistema emprendedor y economía del conocimiento.',
     'https://www.cordoba.gob.ar', 'economia@cordoba.gob.ar', '+54 351 428-5600', 2000,
     '{"type": "municipality_office"}'::jsonb);

-- === ENABLERS (ids 20-23) ===
INSERT INTO actors (id, name, description, website, email, phone, year_created, metadata) VALUES
(20, 'Incubadora UNC',
     'Incubadora de empresas de la UNC. Acompaña emprendimientos deep tech desde la universidad.',
     'https://www.incubadoraunc.edu.ar', 'incubadora@unc.edu.ar', '+54 351 433-4444', 2002,
     '{"services": ["incubation", "mentoring", "lab_access"]}'::jsonb),

(21, 'CorLab - Laboratorio de Innovación Abierta',
     'Laboratorio de innovación de la Municipalidad de Córdoba.',
     'https://www.corlab.cordoba.gob.ar', 'info@corlab.gob.ar', '+54 351 428-5678', 2014,
     '{"services": ["acceleration", "co-working", "networking"]}'::jsonb),

(22, 'Parque Tecnológico del Suquía',
     'Parque tecnológico provincial. Infraestructura y servicios para empresas de base tecnológica.',
     'https://www.parquetecnologico.cba.gov.ar', 'info@parquetec.cba.gov.ar', '+54 351 480-9000', 2010,
     '{"services": ["infrastructure", "fiscal_benefits", "networking"], "type": "technology_park"}'::jsonb),

(23, 'Fundación Córdoba TIC',
     'Cluster TIC que articula empresas tecnológicas con academia y gobierno.',
     'https://www.cordobatic.org', 'info@cordobatic.org', '+54 351 460-3500', 2001,
     '{"services": ["networking", "training", "market_access"], "type": "cluster"}'::jsonb);

-- === FUNDS (ids 24-26) ===
INSERT INTO actors (id, name, description, website, email, phone, year_created, metadata) VALUES
(24, 'CITES Fund',
     'Fondo de inversión del Centro de Innovación Tecnológica Empresarial y Social.',
     'https://www.citesarg.com', 'info@citesarg.com', '+54 351 568-2200', 2012,
     '{"fund_type": "accelerator_fund", "scope": "national", "ticket_range": "USD 50k - 500k"}'::jsonb),

(25, 'Patagonia Ventures',
     'Fondo de venture capital con foco en deep tech latinoamericana.',
     NULL, 'deals@patagoniaventures.com', NULL, 2019,
     '{"fund_type": "vc", "scope": "international", "ticket_range": "USD 200k - 2M"}'::jsonb),

(26, 'Red de Inversores Ángeles de Córdoba',
     'Red de inversores ángeles que financia startups en etapa pre-seed y seed.',
     'https://www.riacordoba.com.ar', 'contacto@riacordoba.com.ar', NULL, 2015,
     '{"fund_type": "angel_network", "scope": "provincial", "ticket_range": "USD 10k - 100k"}'::jsonb);

-- Reset sequences after explicit ID inserts
SELECT setval('actors_id_seq', (SELECT MAX(id) FROM actors));
SELECT setval('actor_types_id_seq', (SELECT MAX(id) FROM actor_types));
SELECT setval('technological_areas_id_seq', (SELECT MAX(id) FROM technological_areas));
SELECT setval('locations_id_seq', (SELECT MAX(id) FROM locations));
SELECT setval('sources_id_seq', (SELECT MAX(id) FROM sources));

-- ============================================================
-- ACTOR TYPE ASSIGNMENTS
-- ============================================================
INSERT INTO actor_type_assignments (actor_id, actor_type_id) VALUES
-- Academia
(1,  1), -- UNC -> Academia
(2,  1), -- CONICET CCT -> Academia
(3,  1), -- UTN-FRC -> Academia
(4,  1), -- UNRC -> Academia
(5,  1), -- UNVM -> Academia
-- Startups
(6,  2), -- BioSeed -> Startup
(7,  2), -- NanoSense -> Startup
(8,  2), -- Visión AI -> Startup
(9,  2), -- FotoMed -> Startup
(10, 2), -- QBit -> Startup
(11, 2), -- AgriDrone -> Startup
(12, 2), -- CompositAR -> Startup
-- Corporations
(13, 3), -- Arcor -> Corporation
(14, 3), -- Stamatti -> Corporation
(15, 3), -- CEA -> Corporation
(16, 3), -- Bioceres -> Corporation
-- Government
(17, 4), -- MinCyT CBA -> Government
(18, 4), -- Agencia I+D+i -> Government
(19, 4), -- Muni CBA -> Government
-- Enablers
(20, 5), -- Incubadora UNC -> Enabler
(21, 5), -- CorLab -> Enabler
(22, 5), -- Parque Tecnológico -> Enabler
(23, 5), -- Córdoba TIC -> Enabler
-- Funds
(24, 6), -- CITES Fund -> Fund
(25, 6), -- Patagonia Ventures -> Fund
(26, 6), -- Red Ángeles -> Fund
-- Dual roles
(1,  5), -- UNC is also an Enabler (has incubator)
(16, 2), -- Bioceres also started as a Startup
(20, 1); -- Incubadora UNC also acts as Academia (research spin-off)

-- ============================================================
-- ACTOR CLASSIFICATIONS
-- ============================================================
INSERT INTO actor_classifications (actor_id, ownership_type_id, organization_level_id, organization_size, notes) VALUES
-- Academia: Public, National
(1,  1, 1, 'Large (>1000 employees)',   'Universidad con 10.000+ empleados'),
(2,  1, 1, 'Large (>1000 employees)',   'Complejo con 22 institutos'),
(3,  1, 1, 'Large (>1000 employees)',   NULL),
(4,  1, 1, 'Medium (100-1000)',         NULL),
(5,  1, 1, 'Medium (100-1000)',         NULL),
-- Government
(17, 1, 2, 'Medium (100-1000)',         'Ministerio provincial'),
(18, 1, 1, 'Medium (100-1000)',         'Agencia nacional'),
(19, 1, 3, 'Small (<100)',              'Secretaría municipal'),
-- Enablers
(20, 1, 1, 'Small (<100)',              'Pertenece a la UNC'),
(21, 1, 3, 'Small (<100)',              'Depende de la Municipalidad'),
(22, 3, 2, 'Medium (100-1000)',         'Gestión mixta pública-privada'),
(23, 2, 2, 'Small (<100)',              'Asociación civil sin fines de lucro');

-- ============================================================
-- ACTOR TECHNOLOGICAL AREAS
-- ============================================================
INSERT INTO actor_technological_areas (actor_id, technological_area_id) VALUES
-- UNC: broad coverage
(1,  1), (1,  2), (1,  3), (1,  4), (1,  5),
-- CONICET: broad coverage
(2,  1), (2,  2), (2,  3), (2,  7),
-- UTN-FRC: engineering focus
(3,  6), (3,  60), (3,  61), (3,  4), (3,  40),
-- UNRC: agri biotech
(4,  1), (4,  12),
-- UNVM: AI applications
(5,  2), (5,  21),
-- Startups: specific sub-areas
(6,  1),  (6,  12),         -- BioSeed: Agri Biotech
(7,  3),  (7,  30),         -- NanoSense: Nanosensors
(8,  2),  (8,  20),         -- Visión AI: Computer Vision
(9,  5),  (9,  11),         -- FotoMed: Photonics + Medical Biotech
(10, 7),                    -- QBit: Quantum
(11, 2),  (11, 22),         -- AgriDrone: Robotics
(12, 4),  (12, 40),         -- CompositAR: Composites
-- Corporations
(13, 1),  (13, 10),         -- Arcor: Industrial Biotech
(14, 4),  (14, 2),          -- Stamatti: Advanced Materials + AI
(15, 6),  (15, 5),          -- CEA: Electronics + Photonics
(16, 1),  (16, 12);         -- Bioceres: Agri Biotech

-- ============================================================
-- ACTOR CAPABILITIES
-- ============================================================
-- capabilities IDs:  1=RRHH, 2=Serv.Esp., 3=I+D, 4=Protección,
--                    5=Infraestructura, 6=Comercialización,
--                    7=Incubación, 8=Aceleración, 9=Creación empresa

INSERT INTO actor_capabilities (actor_id, capability_id, description) VALUES
-- UNC
(1,  1, 'Formación de grado y posgrado en todas las verticales'),
(1,  3, 'Grupos de investigación en biotech, IA, materiales, nano'),
(1,  4, 'Oficina de propiedad intelectual'),
(1,  5, 'Laboratorios de última generación'),
(1,  7, 'Programa de incubación universitaria'),
-- CONICET
(2,  1, 'Investigadores de carrera y becarios doctorales'),
(2,  3, 'Investigación básica y aplicada en CyT'),
(2,  5, 'Infraestructura de laboratorios en institutos asociados'),
-- UTN-FRC
(3,  1, 'Ingenieros y técnicos especializados'),
(3,  2, 'Ensayos de materiales y electrónica'),
(3,  3, 'Grupos de I+D en IoT y electrónica de potencia'),
-- Incubadora UNC
(20, 7, 'Programa de incubación deep tech de 12 meses'),
(20, 8, 'Programa de pre-aceleración'),
(20, 9, 'Acompañamiento en creación de spin-offs'),
-- CorLab
(21, 8, 'Programa de aceleración de innovación abierta'),
-- Parque Tecnológico
(22, 5, 'Oficinas, laboratorios compartidos, salas limpias'),
(22, 2, 'Servicios de prototipado rápido'),
-- Córdoba TIC
(23, 6, 'Acceso a mercados nacionales e internacionales'),
(23, 1, 'Capacitación y formación continua'),
-- CITES Fund
(24, 8, 'Programa de aceleración con inversión'),
(24, 9, 'Creación de nuevas empresas desde el fondo'),
-- Visión AI (startup madura)
(8,  2, 'Servicios de inspección visual automatizada'),
(8,  6, 'Venta de licencias de software B2B');

-- ============================================================
-- ACTOR LOCATIONS
-- ============================================================
INSERT INTO actor_locations (actor_id, location_id, location_type) VALUES
-- Academia
(1,  1,  'headquarters'),   -- UNC -> Ciudad Universitaria
(2,  1,  'headquarters'),   -- CONICET CCT -> Ciudad Universitaria
(3,  2,  'headquarters'),   -- UTN-FRC
(4,  7,  'headquarters'),   -- UNRC -> Río Cuarto
(5,  8,  'headquarters'),   -- UNVM -> Villa María
-- Startups
(6,  1,  'headquarters'),   -- BioSeed -> Lab en Ciudad Universitaria
(7,  5,  'headquarters'),   -- NanoSense -> Parque Industrial
(8,  6,  'headquarters'),   -- Visión AI
(8,  9,  'branch'),         -- Visión AI -> oficina comercial CABA
(9,  1,  'headquarters'),   -- FotoMed -> Ciudad Universitaria
(10, 10, 'headquarters'),   -- QBit
(11, 3,  'headquarters'),   -- AgriDrone
(12, 5,  'headquarters'),   -- CompositAR -> Parque Industrial
-- Corporations
(13, 4,  'branch'),         -- Arcor -> oficina Córdoba
(14, 11, 'headquarters'),   -- Stamatti
(15, 5,  'headquarters'),   -- CEA -> Parque Industrial
(16, 9,  'branch'),         -- Bioceres -> oficina CABA
-- Government
(17, 12, 'headquarters'),   -- MinCyT CBA
(18, 9,  'headquarters'),   -- Agencia I+D+i -> CABA
(19, 12, 'headquarters'),   -- Muni CBA
-- Enablers
(20, 1,  'headquarters'),   -- Incubadora UNC -> Ciudad Universitaria
(21, 12, 'headquarters'),   -- CorLab
(22, 5,  'headquarters'),   -- Parque Tecnológico
(23, 6,  'headquarters'),   -- Córdoba TIC
-- Funds
(24, 4,  'headquarters'),   -- CITES
(25, 9,  'headquarters'),   -- Patagonia Ventures -> CABA
(26, 10, 'headquarters');   -- Red Ángeles CBA

-- ============================================================
-- RELATIONSHIPS (inter-actor linkages)
-- ============================================================
-- relationship_types IDs: 1=RRHH, 2=Serv.Esp., 3=I+D,
--     4=Protección, 5=Infraestructura, 6=Comercialización,
--     7=Incubación, 8=Aceleración, 9=Creación empresa

INSERT INTO relationships (source_actor_id, target_actor_id, relationship_type_id, description, start_date) VALUES
-- UNC incubates startups
(1,  6,  7, 'BioSeed fue incubada en la Incubadora UNC',                '2021-03-01'),
(1,  9,  7, 'FotoMed fue incubada en la Incubadora UNC',                '2023-06-01'),
(1,  10, 7, 'QBit Security fue incubada en la Incubadora UNC',          '2024-01-15'),
-- UNC provides RRHH to corporations
(1,  13, 1, 'Convenio de pasantías e inserción laboral con Arcor',      '2018-01-01'),
(1,  15, 1, 'Formación de ingenieros para proyectos satelitales',       '2010-06-01'),
-- CONICET I+D agreements
(2,  6,  3, 'Convenio de investigación en edición genética CRISPR',     '2022-01-01'),
(2,  7,  3, 'Proyecto PICT conjunto en nanosensores de agua',           '2023-01-01'),
(2,  16, 3, 'Investigación conjunta en biocontroladores',               '2019-06-01'),
-- UTN provides specialized services
(3,  12, 2, 'Ensayos mecánicos de materiales compuestos para CompositAR','2020-03-15'),
(3,  14, 2, 'Consultoría en materiales avanzados para Stamatti',        '2021-07-01'),
-- Government funds research
(18, 6,  3, 'FONTAR: subsidio ANR para desarrollo de BioSeed',         '2022-06-01'),
(18, 7,  3, 'FONCYT: PICT Startup para NanoSense Tech',                '2024-01-01'),
(17, 22, 5, 'Financiamiento provincial para el Parque Tecnológico',     '2010-01-01'),
-- Enablers accelerate/incubate
(20, 6,  7, 'Programa de incubación 2021 - BioSeed',                   '2021-03-01'),
(20, 9,  7, 'Programa de incubación 2023 - FotoMed',                   '2023-06-01'),
(21, 8,  8, 'Programa CorLab Acelera 2020 - Visión AI',                '2020-08-01'),
(21, 11, 8, 'Programa CorLab Acelera 2021 - AgriDrone',                '2021-03-01'),
-- Parque Tecnológico provides infrastructure
(22, 7,  5, 'Laboratorio compartido para NanoSense',                    '2023-06-01'),
(22, 12, 5, 'Nave industrial para CompositAR',                         '2019-01-01'),
-- Funds invest in startups
(24, 8,  8, 'Inversión seed + aceleración Visión AI',                   '2020-01-01'),
(24, 11, 8, 'Inversión pre-seed AgriDrone',                             '2021-06-01'),
(25, 8,  9, 'Series A liderada por Patagonia Ventures',                 '2022-09-01'),
(25, 12, 9, 'Series A co-inversión CompositAR',                         '2023-03-01'),
(26, 6,  9, 'Inversión ángel en ronda pre-seed BioSeed',               '2021-01-15'),
(26, 9,  9, 'Inversión ángel en FotoMed',                               '2023-07-01'),
-- Corporations engage with startups
(13, 6,  3, 'Piloto de bioenzimas BioSeed para procesos Arcor',        '2024-01-01'),
(15, 8,  2, 'Contrato de visión artificial para control satelital',     '2023-09-01'),
(16, 6,  3, 'Co-desarrollo de variedades editadas con BioSeed',        '2023-06-01');

-- ============================================================
-- ACTOR SOURCES (linking actors to data sources)
-- ============================================================
INSERT INTO actor_sources (actor_id, source_id, description) VALUES
-- CONICET registry
(2,  1, 'Listado oficial de institutos CONICET en Córdoba'),
(1,  1, 'Institutos de doble dependencia UNC-CONICET'),
-- Crunchbase for startups
(6,  2, 'Perfil de BioSeed Genomics en Crunchbase'),
(8,  2, 'Perfil de Visión AI en Crunchbase'),
(11, 2, 'Perfil de AgriDrone CBA en Crunchbase'),
(12, 2, 'Perfil de CompositAR en Crunchbase'),
-- ADEC for corporations
(13, 3, 'Ficha de Arcor en directorio ADEC'),
(14, 3, 'Ficha de Stamatti en directorio ADEC'),
-- MinCyT for enablers and government
(17, 4, 'Información institucional del ministerio'),
(22, 4, 'Registro del Parque Tecnológico en el MinCyT'),
-- Mapa Biotech
(6,  5, 'BioSeed relevada en Mapa Biotech Argentina 2025'),
(16, 5, 'Bioceres en Mapa Biotech Argentina 2025'),
-- LinkedIn
(8,  6, 'Perfil LinkedIn de Visión AI'),
(24, 6, 'Perfil LinkedIn de CITES Fund'),
-- Endeavor
(8,  7, 'Visión AI en portafolio Endeavor Argentina');

-- ============================================================
-- FOUNDING SOURCES
-- ============================================================
INSERT INTO funding_sources (name) VALUES
('FFF (Family, Friends & Fools)'),
('Inversores Ángeles'),
('Fondos de Capital de Riesgo (VC)'),
('Subsidios Estatales / Programas Públicos'),
('Programas de Aceleración'),
('Capital Privado de Socios'),
('Organismos Internacionales'),
('Presupuesto Propio / Membresías');

-- Link actors to funding sources
INSERT INTO actor_funding_sources (actor_id, funding_source_id) VALUES
(6,  1), -- BioSeed: FFF
(6,  2), -- BioSeed: Inversores Ángeles
(6,  4), -- BioSeed: Subsidios Estatales
(7,  1), -- NanoSense: FFF
(7,  4), -- NanoSense: Subsidios Estatales
(8,  3), -- Visión AI: Fondos VC
(8,  5), -- Visión AI: Programas de Aceleración
(9,  1), -- FotoMed: FFF
(9,  4), -- FotoMed: Subsidios Estatales
(10, 1), -- QBit: FFF
(11, 2), -- AgriDrone: Inversores Ángeles
(11, 4), -- AgriDrone: Subsidios Estatales
(12, 3); -- CompositAR: Fondos VC

-- ============================================================
-- FUNDING EVENTS & PARTICIPANTS
-- ============================================================
INSERT INTO funding_events (id, recipient_actor_id, round_type, amount, currency, event_date, description) VALUES
(1, 6,  'Pre-Seed', 80000.00,  'USD', '2021-06-15', 'Ronda pre-semilla de BioSeed Genomics'),
(2, 8,  'Seed',     250000.00, 'USD', '2020-02-01', 'Ronda semilla Visión AI'),
(3, 8,  'Series A', 1500000.00,'USD', '2022-09-10', 'Serie A Visión AI'),
(4, 12, 'Series A', 1000000.00,'USD', '2023-03-20', 'Serie A CompositAR');

INSERT INTO funding_event_participants (funding_event_id, investor_actor_id, role, amount_contributed) VALUES
-- Event 1: BioSeed Pre-Seed
(1, 26, 'Lead Investor', 50000.00),  -- Red Ángeles CBA
(1, 18, 'Grantor',       30000.00),  -- Agencia I+D+i (FONTAR)
-- Event 2: Visión AI Seed
(2, 24, 'Lead Investor', 250000.00), -- CITES Fund
-- Event 3: Visión AI Series A
(3, 25, 'Lead Investor', 1500000.00),-- Patagonia Ventures
-- Event 4: CompositAR Series A
(4, 25, 'Lead Investor', 1000000.00);-- Patagonia Ventures

SELECT setval('funding_events_id_seq', (SELECT MAX(id) FROM funding_events));

COMMIT;