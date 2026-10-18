# Governance

This project currently has one maintainer, and pretending otherwise would be worse than saying so.

The relevant risk for assistive technology is not bad code. It is that a person comes to depend on something that stops being maintained. Everything below exists to make that dependency survivable.

## Now: one maintainer

Decisions are made by the maintainer. Disagreements are resolved by writing down the reasoning in the issue, and by the rule that a change must be reversible by one config edit or one revert.

## Trigger for a second maintainer

The second maintainer is appointed when **any** of these is true:

1. a contributor has landed three merged pull requests of substance; or
2. the project has one documented user (not an author) relying on it daily; or
3. the maintainer has been unresponsive for 30 consecutive days.

(1) and (2) are proactive. (3) is the fallback that makes the project inheritable rather than merely open.

When it happens: a second admin is added, the repository moves to an organisation account, and `CODEOWNERS` gains both names. Until then the single-maintainer structure is stated here rather than implied by a repository owner field.

## Handover requirements

Anything that would make the project impossible to hand over is treated as a bug.

- **Bus factor check, each release:** could a competent Python developer who has never spoken to the maintainer release this, using only the repository? If the answer is no, the release is blocked and the missing documentation is written first.
- **No private infrastructure.** Nothing in the release path depends on the maintainer's machine.
- **No unexplained components.** Every module that carries a non-obvious decision has the reason in a comment next to it, not in a person's head.
- **Recorded sessions beat recreations.** A recorded landmark file plus `ito replay` lets a successor reproduce a threshold decision without asking a user to perform the movement again. `ito calibrate` writes the session it measured next to the fitted config for exactly this reason; do not delete one and keep the other.

## How the project can be replaced

This is a stated goal, not a hedge. The MQTT contract is documented ([hardware.md](docs/hardware.md)) precisely so that a user can point Home Assistant at the topic and drop the mapping layer entirely. A user who can leave easily is a user who can trust the thing they stay for.

## Funding

There is none, and there is no plan to charge users. The realistic paths, in order of preference:

1. grants for assistive technology or open hardware;
2. paid support and custom deployments for institutions — rehabilitation centres, care homes, special-needs schools — which fund the free version without gating it;
3. nothing, and the project stays small.

No path makes a core feature conditional on payment. If that ever changes it will be a new project with a different name, not a quietly relicensed one.

## Changes to this document

Open an issue. Governance changes are the one category where the maintainer will wait for comment before merging.
