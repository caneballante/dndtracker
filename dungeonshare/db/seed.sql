insert into dungeonshare.campaigns (
  slug, name, eyebrow, tagline, summary, accent
)
values
  (
    'three-friends',
    'The Three Friends',
    'A road-worn company',
    'Three unlikely companions, one very long road.',
    'Session recaps, allies, artifacts, and the clues still troubling the party.',
    'oxblood'
  ),
  (
    'tomb-of-annihilation',
    'Tomb of Annihilation',
    'Expedition journal',
    'Heat, hexes, and the long shadow of the Soulmonger.',
    'A field record of Chult: discoveries, dangers, faces, and hard-won survival.',
    'forest'
  )
on conflict (slug) do nothing;
