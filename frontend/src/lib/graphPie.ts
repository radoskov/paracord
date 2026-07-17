/**
 * Multi-color "color wheel" node symbols for ECharts graphs.
 *
 * ECharts has no native multi-color node, but any series symbol can be an image — so a node with
 * several memberships (a paper on multiple shelves/racks, with multiple tags) renders as a small
 * SVG pie, one equal segment per membership color, passed as `symbol: 'image://data:…'`.
 * Single-color nodes should keep the plain 'circle' symbol (cheaper, crisper) — callers only use
 * this for 2+ colors.
 */

const SIZE = 64; // SVG viewport; ECharts scales it to symbolSize, so it only sets crispness.

function polar(cx: number, cy: number, r: number, angle: number): [number, number] {
  return [cx + r * Math.sin(angle), cy - r * Math.cos(angle)];
}

/** An `image://` data-URI symbol: a circle split into equal segments, one per color (clockwise
 * from 12 o'clock). One color yields a plain filled circle. */
export function pieSymbol(colors: string[], borderColor = 'rgba(0,0,0,0.25)'): string {
  const cx = SIZE / 2;
  const r = SIZE / 2 - 1.5;
  let shapes = '';
  if (colors.length <= 1) {
    shapes = `<circle cx="${cx}" cy="${cx}" r="${r}" fill="${colors[0] ?? '#999'}"/>`;
  } else {
    const step = (2 * Math.PI) / colors.length;
    shapes = colors
      .map((color, i) => {
        const [x1, y1] = polar(cx, cx, r, i * step);
        const [x2, y2] = polar(cx, cx, r, (i + 1) * step);
        const largeArc = step > Math.PI ? 1 : 0;
        return (
          `<path d="M${cx},${cx} L${x1.toFixed(2)},${y1.toFixed(2)} ` +
          `A${r},${r} 0 ${largeArc} 1 ${x2.toFixed(2)},${y2.toFixed(2)} Z" fill="${color}"/>`
        );
      })
      .join('');
  }
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${SIZE} ${SIZE}">` +
    `${shapes}<circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="${borderColor}" stroke-width="1"/></svg>`;
  return `image://data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
