# Arentor Authoritative Source Sync Patch — 2026-09-04

This checklist exists because the reviewed JSON now contains DM-approved corrections/additions that are not all present in the current authoritative DOCX. Apply these to the authoritative source before final promotion.

## Required corrections

- Replace stale references to **Chancellor Vayne** with **Councilor Vayne** where they refer to Algren Vayne.
- Remove the standalone **Chancellor** office/title as an Arentorian office unless later explicitly reintroduced.
- Remove/replace **Chancellor’s Office** wording without inventing a new office; describe Marcel Dennet as a senior record-keeper in the House of Crowns administrative offices where necessary.
- Establish **Moon Castle** as the proper name of the sealed royal castle; use **Royal Castle / royal castle / the castle / sealed castle** descriptively.
- Establish **Blanders Everything Institute of Dungeoneering** as the formal name; retain **Blanders** and **Blanders School of Adventuring** as common/descriptive names.
- Remove the section-label interpretation of **Beyond the Walls / Caravan and Farm Belt** as a formal named place; keep its descriptive outskirts material in prose.
- Keep **South Gate** and **Forgotten Royal Storeroom** as named places.

## Fact-level visibility corrections

- Public Branna Coalvein fact: Curator/public director of Stonehome Heritage House.
- DM-only Branna fact: she is First Auditor of the Deep Ledger.
- Public Stonehome description must not reveal the Deep Ledger connection.
- Deep Ledger and First Auditor remain DM-only.

## Schema / taxonomy decisions to reflect in derived data

- `supernatural_entity`: Azhurath, Garagos.
- `magical_infrastructure`: Royal Paths, Royal Ward Network.
- `symbol`: Eight-pointed star of the royal line, Banner of Arentoria, Seal of Arentoria.
- `business`: Brindlewink — Lodgings, Lettings & Leasehold Advisory.
- `family`: Brockpuddle family.
- `household`: Kerrin household.
- `item`: Blanders Credential Pin, Silverwood fruit.
- `creature`: supports durable named/recurring creatures such as Varney and persistent construct groups.
- Reserve `family_house` for actual dynastic/noble houses.

## Silverwood fruit

Add the DM-approved canon that Silverwood fruit is apple-like, has healing powers, and can allow one to traverse gates without a key. `Silverwood apple` is a useful specific alias; avoid the generic alias `apple` for retrieval.

## King's Spire durable additions approved for world canon

- **King's Spire** is an ancient sealed royal spire and a **Royal Path nexus**.
- At King's Spire, a Royal Path endpoint can appear as a **black mirror**; local operation can be one-way until a linked portal or keystone is activated.
- The **Royal Ward Network** manifests there as a blue nimbus and can suffer a localized breach while the wider network remains active.
- **Varney** is a large black royal guardian dog at King's Spire; a private royal-era portrait depicts the same dog younger beside Aerlan. His longevity is unexplained.
- **Seralith’s Testament** exists as a named final testament written shortly before the spire was sealed. Do not import older conflicting full testament text unless separately approved.
- **King's Spire Nexus Chamber / Tree Sanctuary** is durable geography containing a living tree that anchors a black-mirror Royal Path portal. The tree’s degree of sentience remains unresolved.
- **King's Spire Royal Kitchen Attendants** are three non-sentient royal kitchen constructs built for cooking, private-chamber maintenance and emergency response. Later party possession/location must come from session history, not from the adventure source alone.

## Historical uncertainty guardrails

- Seralith disappeared; exact later ascension mechanics unresolved.
- Aerlan disappeared; exact later ascension mechanics unresolved.
- Rostan Varnholde betrayed them at Fort Aelwind; final fate unresolved.
- Ghurzag is only a distant prophetic war figure in current Master Canon; do not import ancestry, title, present location, demon status or legacy backstory.
- Use **Betrayal at Fort Aelwind** as a database label only; do not imply it is an official historical title.
