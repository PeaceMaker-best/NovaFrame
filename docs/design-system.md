# NovaFrame Visual System

NovaFrame is a production workstation, not a generic admin dashboard. Its visual system should make product imagery feel central while keeping batch status, evidence, and safety constraints easy to scan during long working sessions.

## Design character

- **Calm craft:** warm off-white surfaces, restrained shadows, and dark green production chrome.
- **Product-first:** generated and source imagery carries more visual weight than decorative UI.
- **Confident hierarchy:** page titles, decisions, and primary actions are unmistakable; metadata stays quieter without becoming tiny.
- **Truthful state:** loading, empty, offline, blocked, staged, and promoted states use distinct language and appearance.
- **Focused energy:** lime is reserved for generative and approval actions; teal communicates structure, selection, and progress.

## Core tokens

| Role | Direction |
| --- | --- |
| Workspace background | warm neutral `#eeede8` |
| Primary surface | soft white `#fffefa` |
| Production chrome | deep green-black `#17201c` |
| Structural accent | teal `#176b60` |
| Generative accent | lime `#cdeb7f` |
| Destructive state | muted brick `#b34e48` |
| Supporting accents | coral and sand, used sparingly |

Surfaces use 16–24 px radii according to scale. Shadows are broad and low contrast; borders still define functional grouping when shadows disappear on lower-quality displays.

## Type and density

- Page titles: 26 px.
- Overview statement: responsive 38–52 px.
- Section titles: 20–21 px.
- Primary row labels: 14–16 px.
- Standard UI copy: 13–14 px.
- Metadata: normally 12 px and never reduced merely to fit more columns.
- Primary controls: 42–46 px high; compact icon tools remain at least 36–40 px where practical.

Dense production tables may scroll horizontally. They should not compress meaningful labels into unreadable typography.

## Layout behavior

- Overview, matrix, assets, and queue pages use the available content width up to 2200 px.
- At wide breakpoints, extra space increases image and card size instead of becoming inert side margins.
- At narrower desktop widths, metric cards and major page columns reflow before typography shrinks.
- The Studio artboard remains the visual center. Asset and generation panels can be collapsed independently for focused inspection and composition work.

## Studio rules

- A new real canvas starts blank and explains how to begin; it never inserts a demo image automatically.
- Empty-state guidance floats over the workspace without being saved into the document.
- Left and right panels are independent tools, not permanent layout tax.
- The dot grid and surrounding chrome stay lower contrast than the artboard.
- Generation, save, loading, and preflight states remain visible without covering the artwork.

## Production pages

- Matrix: emphasize selected scope and blocked cells; keep the sticky submission bar visually decisive.
- Queue: use stable rows, readable progress, and restrained status pills instead of decorative animation.
- Review: dark comparison space isolates images; selection lime must be visible at a glance; permanent actions remain separated from cleanup.
- Assets: source imagery uses larger editorial tiles and honest manifest state rather than simulated facts.

## Motion and accessibility

- Hover lift is limited to a few pixels and used only on navigable cards or assets.
- Focus-visible outlines remain mandatory.
- Reduced-motion preferences disable nonessential animation and transitions.
- State is never conveyed by color alone; labels and icons accompany status colors.

