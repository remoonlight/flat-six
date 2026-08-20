Locator overview inbox (passive)

Drop your own PETKA screenshots here — agent/scripts never drive PETKA GUI.

Filenames (any of):
  engine-bay.png | engine-bay.jpg | engine-bay-overview.png
  brakes.png / chassis.png  (same pattern)
  or subdir:  engine-bay/overview.png

Processed files move to done/. Destination:
  data/petka/<zone>/shots/overview.<ext>

Commands (repo root):
  npm run watch:locator-inbox
  npm run ingest:locator-inbox
  node scripts/ingest-locator-inbox.mjs --dry-run

Do not commit large real screenshots.