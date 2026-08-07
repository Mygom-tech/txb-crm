Place your licensed Roobert font files in this directory using these filenames:\n\nRoobert-Light.woff2\nRoobert-Light.otf\nRoobert-Light.ttf\nRoobert-Regular.woff2\nRoobert-Regular.otf\nRoobert-Regular.ttf\n\nOnly one supported format per weight is required. WOFF2 is preferred.\nEOF

# Update project readme if present
cat > /mnt/data/ttb-landing/README.md <<'EOF'
# Ten Times Better — parked domain landing page

Static HTML/CSS/JS landing screen based on the supplied desktop and mobile references.

## Run
Open `index.html` directly or serve the folder from any static host.

## Fonts
Put your licensed Roobert font files in `assets/fonts/`.

The CSS is configured for:
- `Roobert-Light.woff2` (preferred), with `.otf` and `.ttf` fallbacks
- `Roobert-Regular.woff2` (preferred), with `.otf` and `.ttf` fallbacks

Only one supported format for each weight is required.

## Motion
GSAP is loaded from jsDelivr. The intro includes the text reveal and stripe rise/unfold sequence. On fine-pointer devices, the cursor has a subtle ambient halo and nearby stripes gently swell as the cursor approaches them. Reduced-motion users receive the static final composition.
