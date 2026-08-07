# Ten Times Better — parked-domain landing screen

A zero-build static landing screen based on the supplied XD reference.

## Run locally

You can open `index.html` directly, or serve the directory with any static web
server.

For example:

```bash
python -m http.server 8080
```

Then visit `http://localhost:8080`.

## Fonts

See `assets/fonts/README.txt`.

## Animation

GSAP is loaded from jsDelivr. The page remains fully visible if the CDN fails,
and animation is skipped for visitors who prefer reduced motion.
