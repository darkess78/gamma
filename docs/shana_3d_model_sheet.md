# Shana 3D model sheet and VRoid build guide

Source art: `images/shana/` (eight 1024x1536 full-body PNGs: jacket and no
jacket, each with mouth open/closed and eyes open/closed). Colours below were
sampled from those images and rounded; treat them as starting points and
adjust by eye under VRoid's lighting. Spec and runtime wiring:
[specs/avatar_3d.md](../specs/avatar_3d.md).

## 1. Character summary

A slim schoolgirl with very long straight black hair, amber eyes and a
serious, slightly sulky resting face. Green-and-white Japanese school uniform
in two layers: a green blazer over a white sailor blouse with a yellow
neckerchief. Anime proportions, about 5.8 heads tall, long legs.

## 2. Proportions (from the front view)

Measured on the 1536 px tall image; the figure is about 1380 px from crown to
sole.

| Landmark | Height from top of figure | Share of height |
|---|---|---|
| Crown (ahoge excluded) | 0 | 0% |
| Eyes | ~165 px | 12% |
| Chin | ~240 px | 17% (head ≈ 1/5.8 of height) |
| Shoulders | ~300 px | 22% |
| Waist / blazer hem | ~630 px | 46% |
| Skirt hem (mid-thigh) | ~780 px | 57% |
| Knees | ~1020 px | 74% |
| Sock tops | ~1200 px | 87% |
| Soles | ~1380 px | 100% |

- Narrow shoulders (about 1.5 head widths), small bust, slim waist and hips.
- Long, slim legs; crotch roughly at 55% of height.
- Arms hang straight; hands relaxed with loosely curled fingers.
- Small, slightly pointed chin; small nose (a single highlight line); small mouth.

## 3. Hair

- **Colour:** near-black with a cool grey sheen. Base `#1B1A1E`, mid
  `#2D2D2F`, highlight `#4A4850`. No coloured streaks.
- **Length:** very long; the back reaches mid-thigh (about the skirt hem).
- **Shape:** straight, heavy, splits into separate pointed locks toward the
  ends; outer locks flare slightly away from the body at the bottom.
- **Bangs:** choppy, eyebrow length, uneven points. One longer strand falls
  from the centre parting across the nose bridge to about cheek level.
- **Side locks:** long locks in front of the ears frame the face and fall over
  the chest, down to roughly the waist.
- **Ahoge:** one tall curved strand at the crown leaning to her right, with a
  small second strand beside it.
- **Accessory:** a hair clip on her left side (viewer's right), above the
  temple: two short parallel bars, white/silver `#EEEEEE`, angled slightly
  upward toward the back.

## 4. Face and eyes

- **Eyes:** large, slightly upturned outer corners, sharp upper lash line.
  Iris amber/honey: upper `#8A4F12`, mid `#D9962B`, lower highlight `#F3BB4F`;
  pupil dark brown `#3B1D0A`; one white highlight upper-left and a small one
  lower-right. Sclera white `#FFFFFF` with a faint grey upper shadow.
- **Lashes and lines:** black `#1B1A1E`, thick upper line, thin lower line.
- **Brows:** thin, black, slightly lowered toward the centre, which gives the
  default serious look.
- **Skin:** base `#FDE8D8`, shadow `#F2C4B0`, deep shadow (neck under chin)
  `#DF9F7F`, light blush `#F6B5A8` on the cheeks.
- **Mouth:** small. Closed: a short flat line. Open: a rounded open mouth
  showing pink interior `#E57A7A` and tongue `#F09A95`; top teeth hinted.
- **Resting expression:** neutral-serious, mouth closed and flat, brows
  slightly drawn. Not smiling.

## 5. Outfit

### Layer 1: base (no jacket)

| Part | Description | Colour |
|---|---|---|
| Blouse | White short-sleeve sailor blouse, loose fit, one breast pocket on her left, tucked into the skirt | `#F7F8FB`, shadow `#D9DCE6` |
| Sailor collar | Green square sailor collar over the shoulders and back, with two thin white stripes along the edge | `#3F7A5A`, stripes `#F2F4F2` |
| Neckerchief | Yellow-orange scarf tied at the sternum, held by a small green band/knot, two pointed tails to mid-chest | `#F5BE4A`, shadow `#D9962B`, band `#3F7A5A` |
| Skirt | Green pleated skirt, high waistband, even box pleats, hem at mid-thigh | `#3C6B50`, shadow `#2B5039` |
| Socks | White crew socks to mid-calf | `#FAFAFA` |
| Shoes | Brown penny loafers, low heel, glossy | `#5A2A16`, highlight `#8A4A2A`, sole `#2A160C` |

### Layer 2: blazer (jacket on)

| Part | Description | Colour |
|---|---|---|
| Blazer | Green single-breasted blazer, fitted, hem at the hip covering the skirt waistband | `#36704F`, shadow `#24533A` |
| Trim | Thin white piping on lapels, collar edge and pocket flaps; two white stripes around each cuff | `#F2F4F2` |
| Buttons | Three gold buttons down the front | `#E8C878`, shadow `#B8913F` |
| Pockets | Two flap pockets at hip height | flap piping `#F2F4F2` |
| Visible underneath | White blouse collar and the yellow neckerchief knot at the V of the lapels | as layer 1 |

With the blazer on, the sailor collar is hidden under it; only the white
collar points and the yellow knot show.

## 6. Expressions Gamma uses

Gamma's emotion labels (`ALLOWED_EMOTIONS` in
`src/gamma/conversation/shared.py`) and what each should look like. The
adapter mixes VRM preset expressions, so the look depends on how you tune
those presets in VRoid (section 7, step 6).

| Gamma label | Look | VRM 0.x weights Gamma sends | VRM 1.0 weights |
|---|---|---|---|
| neutral | Resting serious face | all zero | all zero |
| happy | Real smile, eyes softened | Joy 1.0 | happy 1.0 |
| teasing | Smug half-lidded smile, one eye narrower | Fun 0.7, Blink_R 0.5 | relaxed 0.7, blinkRight 0.5 |
| concerned | Inner brows raised, mouth slightly down | Sorrow 0.7 | sad 0.7 |
| excited | Wide bright eyes, open smile | Joy 0.7, Surprised 0.6 | happy 0.7, surprised 0.6 |
| embarrassed | Flustered half smile, worried brows | Joy 0.35, Sorrow 0.4 | happy 0.35, sad 0.4 |
| annoyed | Brows down hard, pout, glare | Angry 0.8 | angry 0.8 |

Mouth shapes for speech: A, I, U, E, O (VRM 1.0: aa, ih, ou, ee, oh).
Blink: Blink (VRM 1.0: blink). Gamma also blinks her every 2.5 to 6 seconds.

The blendshape names are case sensitive over VMC. VRoid exports these exact
names, so a stock VRoid model works with no renaming.

## 7. Building her in VRoid Studio

VRoid Studio is free (vroid.com or Steam, Windows/macOS). Menu names below may
differ slightly between versions.

1. **New model.** Start a new female model. Body tab: lower height a little,
   narrow the shoulders, slim the waist and arms, lengthen the legs, keep the
   bust small. Compare against section 2 using the front camera.
2. **Face.** Face tab: pick a sharper, slightly upturned eye shape; set the
   iris to the amber gradient (paint or recolour the default iris texture with
   the section 4 colours). Lower the inner ends of the brows. Small mouth and
   nose. Set skin colour `#FDE8D8`.
3. **Hair.** Hairstyle tab: start from a long straight preset, then use the
   hair-guide tools to:
   - lengthen the back hair to mid-thigh and split the tips into pointed locks;
   - add choppy bangs with one long centre strand across the nose;
   - add long front side locks down to the waist;
   - add an ahoge (a single curved strand group at the crown).
   Set hair colour `#1B1A1E` with a grey highlight `#4A4850`.
   Enable hair physics (bones) on the back hair and side locks; keep the bangs
   stiffer.
4. **Hair clip.** Add it as an accessory if your version has one, or model two
   thin white boxes as a hair group placed above her left temple.
5. **Outfit, two versions.** Do the base first:
   - Tops: a sailor/school shirt preset; recolour body white, collar
     `#3F7A5A` with two white stripes, add the yellow neckerchief (paint it on
     the texture if there is no tie part). Short sleeves.
   - Bottoms: pleated skirt `#3C6B50`, mid-thigh.
   - Socks: white crew. Shoes: brown loafers.
   Save the project, then **Save As** a second project for the jacket version
   and switch the top to a blazer preset: green `#36704F`, white piping,
   three gold buttons, white cuff stripes.
6. **Expressions.** In the face editing / expression settings, tune each
   preset so it matches section 6 on its own at full strength:
   - Joy → happy smile. Angry → annoyed glare and pout.
     Sorrow → concerned brows. Fun → smug half-lidded smile.
     Surprised → wide eyes and raised brows.
   - Check A/I/U/E/O mouth shapes open cleanly without teeth clipping.
   - Check Blink, Blink_L and Blink_R close fully.
7. **Export.** Camera/Export → Export as VRM:
   - **VRM version: VRM 0.0** (works in both Warudo and VSeeFace). If you
     export VRM 1.0 instead, set `naming = "vrm1"` in Gamma's `[avatar3d]`.
   - Title "Shana", author your name, permissions as you like.
   - Polygon reduction: modest (keep hair cross-sections; reduce if Warudo
     is slow). Texture atlas 2048. Material count can be reduced.
   - Do not turn on any option that removes or merges facial blend shapes.
   - Export both versions: `Shana_jacket.vrm` and `Shana_nojacket.vrm`.
8. **Test.** Load the VRM in the avatar app (see the spec), then from any
   machine with Gamma installed run
   `python -m gamma.performer.avatar3d --host <avatar PC IP>`. It cycles every
   emotion and speaks a line in each. Fix any expression that looks wrong in
   VRoid and re-export.

### Check the export

Before setting her up in Warudo or VSeeFace, run the checker on the exported
file (on Gamma PC or anywhere Gamma's code is):

```bash
python -m gamma.performer.vrm_check shana.vrm
```

It says which `avatar3d` naming matches the file, lists any expression or bone
Shana's adapter needs but can't find, reports whether hair and skirt have
spring bones, and shows the licence fields set in VRoid's export dialog.

## 8. Optional extras (need Unity + UniVRM or Blender)

- **Blush.** A real embarrassed look needs a blush clip. Add a custom
  BlendShapeClip (for example `Shana_Blush`) that raises a blush mesh or
  shape key, then map it in `config/app.toml`:
  `[avatar3d.expressions]` `embarrassed = { Joy = 0.3, Shana_Blush = 1.0 }`.
- **Dedicated emotion clips.** Any custom clip name can replace a mapping the
  same way (e.g. `teasing = { Shana_Smug = 1.0 }`).
- **Jacket toggle on one model.** Instead of two VRMs, the jacket mesh can be
  hidden from the avatar app (Warudo can toggle character meshes) or by a
  custom clip.
- **ARKit / Perfect Sync blendshapes** only matter for webcam face tracking,
  which Shana does not use.
