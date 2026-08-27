# Audio sources

## Engine

- `gt86_dyno.mp3`: [FR-S BRZ GT-86 Vortech Supercharged Dyno Run — dezoris](https://freesound.org/people/dezoris/sounds/191194/).
- License: [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).
- The realtime engine layer uses short RPM-indexed loops extracted from the
  documented 3000–7200 RPM fourth-gear dyno pull. Recording credit: dezoris.

## Turbo flutter

The recordings in this directory are distributed under the Pixabay Content License.

- `turbo_flutter.mp3`: [Turbo flutter — spinopel](https://pixabay.com/sound-effects/film-special-effects-turbo-flutter-336362/), published 2025-05-05.
- License: [Pixabay Content License summary](https://pixabay.com/service/license-summary/).

## High-boost air layer

- `compressor_air.mp3`: [short-gas-leak.wav — Astounded](https://freesound.org/people/Astounded/sounds/477846/).
- License: [Creative Commons 0](https://creativecommons.org/publicdomain/zero/1.0/).
- The central, mechanism-free section is used as the real air texture for each
  high-boost compressor pulse.

The files are decoded locally and used as layers in a realtime simulation. They are not redistributed as standalone stock media.

## User-supplied R34 reference

- `user_r34_reference.mp3`: audio extracted locally from
  `/Users/tyakyumyou/Downloads/ターボの音.MOV`, supplied by the user for this
  simulator.
- Acceleration grains use 5.95–7.90 seconds. Brief and normal releases use
  5.38–5.72 and 7.98–8.70 seconds at native speed. The 9.35–10.90 second
  high-boost surge is split into 12 individual 84 ms pressure-pulse grains;
  the realtime model schedules them without whole-clip time compression.
- This recording has no declared redistribution license in the supplied file.
  Keep it local and do not push or redistribute it without permission from the
  rights holder.

## User-supplied multi-turbo reference

- `screen_recording_reference.wav` is extracted locally from
  `/Users/tyakyumyou/Downloads/ScreenRecording_08-26-2026 22-56-14_1.MOV`.
- The user specifically selected the first sound. `turbo_types_intro_reference.wav`
  contains 0.00–6.72 seconds. `turbo_intro_accel_reference.wav` isolates the
  first 0.05–1.80 second spool-up, while `turbo_intro_coast_reference.wav`
  isolates the second clean 5.53–6.28 second rundown.
- Eight short acceleration grains and seven reverse-indexed rundown grains are
  blended by simulated spool. The source audio itself is never reversed or
  globally time-compressed.
- The simulator uses the selected intro only for acceleration/spool character. It
  is not used as lift-off or blow-off audio.
- These files have no declared redistribution license in the supplied video.
  They remain gitignored and must not be pushed without permission.
