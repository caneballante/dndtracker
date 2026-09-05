# Arentor + King's Spire Canon Merge Review — 2026-09-04

## Result

The reviewed Arentor candidate has been updated with the DM decisions from the canon review and a small set of non-conflicting durable facts from `dng_kings_spire.recorder.json`.

This is **not yet the final promoted Master Canon**. Its status is `reviewed_candidate_pending_source_sync` because the authoritative Arentor DOCX still needs to be synchronized with the approved review decisions and the promoted King's Spire facts.

- Starting candidate entities: 138
- Reviewed candidate entities: 139
- Removed as stale/descriptive: 3 (`Beyond the Walls / Caravan and Farm Belt`, `Chancellor’s Office`, `Chancellor`)
- Added from reviewed King's Spire material: 4 (`Varney`, `Seralith’s Testament`, `King's Spire Nexus Chamber`, `King's Spire Royal Kitchen Attendants`)
- Net entity count: 139

## Master Canon promotions from King's Spire

1. **King's Spire** — enriched as an ancient sealed royal spire and Royal Path nexus.
2. **Royal Paths** — enriched with the King's Spire black-mirror endpoint and local one-way/linked-keystone behavior.
3. **Royal Ward Network** — enriched with the King's Spire blue-nimbus manifestation and localized-breach behavior.
4. **Varney** — added as a durable creature identity; black guardian dog, royal-era portrait connection to Aerlan, unexplained longevity.
5. **Seralith’s Testament** — added as a named historical item; only compact facts from the reviewed recorder export are used.
6. **King's Spire Nexus Chamber** — added as durable place/portal geography; tree sentience remains unresolved.
7. **King's Spire Royal Kitchen Attendants** — added because the three non-sentient constructs have campaign persistence beyond a single room.

## Canon-review decisions applied

- Algren Vayne is a **Councilor only**; stale Chancellor terminology is removed.
- `Chancellor` and `Chancellor’s Office` are deleted without inventing a replacement.
- Moon Castle is the proper name; Royal Castle is a descriptive alias.
- Blanders formal name is **Blanders Everything Institute of Dungeoneering**; Blanders and Blanders School of Adventuring remain aliases.
- `supernatural_entity`, `magical_infrastructure`, `symbol`, `business`, `family`, `household`, `item`, and `creature` are supported entity types.
- Branna Coalvein's public Stonehome role is separated from her DM-only Deep Ledger leadership; Stonehome's public description no longer leaks that secret.
- Azhurath and Garagos are `supernatural_entity`.
- Royal Paths and Royal Ward Network are `magical_infrastructure`.
- Eight-pointed star, Banner of Arentoria and Seal of Arentoria are `symbol`.
- Brindlewink is `business`; Brockpuddle is `family`; Kerrin is `household`.
- Blanders Credential Pin and Silverwood fruit are `item`.
- Silverwood fruit is apple-like, heals, and can allow gate traversal without a key; `Silverwood apple` is an alias, but generic `apple` is not.
- South Gate and Forgotten Royal Storeroom remain.
- Beyond the Walls / Caravan and Farm Belt is treated as descriptive prose, not a place identity.
- External Faerûn/Waterdeep/Sword Coast/Trackless Sea/Heral/Brockpuddle material remains only because it has a durable explicit campaign connection.
- Ghurzag remains intentionally sparse; no legacy title, ancestry, location or backstory is imported.
- Seralith, Aerlan and Rostan Varnholde retain their deliberate unresolved historical facts.
- Historical event label normalized to **Betrayal at Fort Aelwind**, without claiming it is an official in-world title.

## King's Spire local reference

The separate reviewed adventure reference keeps the rich local truth: the dining/study, Sky Courier Balcony, bedchamber, kitchen, Nexus Chamber, Forgotten Royal Storeroom, furnishings, portraits, cosmetics, rabbit-folk portfolio, prepared siege, and other room-specific material. It explicitly distinguishes prepared/local truth from session evidence.

## Rejected or held material

- Stale `Council Castle` wording is rejected.
- Ghurzag monster-library lore is rejected from canon import.
- Fort Aelwind monster-library records embedded in the export are excluded.
- Malformed lower-precedence records describing Varney as white or humanoid are rejected in favor of final room prose.
- Portal Glyph-Key is held unresolved because the export is internally inconsistent.
- Bedchamber vestment/tabard and demonic dagger structured loot are rejected as stale against the detailed room prose.
- Morningbell/Thistle/Minister details are held because the source disagrees about two versus three birds.
- Leaf of Renewal remains optional, not canon.
- Exact combat mechanics stay in the original recorder export rather than the canon index.

## Reviewed candidate type counts

- `artifact`: 3
- `business`: 1
- `creature`: 2
- `family`: 1
- `historical_character`: 3
- `historical_event`: 2
- `household`: 1
- `item`: 3
- `magical_infrastructure`: 2
- `organization`: 19
- `person`: 42
- `place`: 36
- `religion`: 2
- `supernatural_entity`: 2
- `symbol`: 3
- `title_office`: 17


## Next gate before final promotion

Synchronize the authoritative `ARENTOR_CANON_SOURCE_FINAL_2026-09-04.docx` with the approved review decisions and the promoted King's Spire durable facts, then update source metadata/hash and run a final validation. Only after that should `arentor_master_canon.json` be promoted as the approved derived view.
