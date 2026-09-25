/**
 * Renders the agent-supplied input-request preview (`preview.blocks`, produced by the
 * SDK's text_block/key_value_block/table_block helpers). Everything is rendered as text:
 * agent strings are never interpreted as HTML.
 */
export type PreviewBlock =
  | { type: 'text'; text: string }
  | { type: 'keyValue'; items: { label: string; value: string }[] }
  | { type: 'table'; columns: string[]; rows: string[][] };

export interface PreviewChoice {
  value: string;
  label: string;
  /** 'primary' | 'secondary' | 'danger' (other values render as secondary). */
  style: string;
}

export interface InputPreview {
  blocks: PreviewBlock[];
  choices: PreviewChoice[];
  consequence: string | null;
}

function str(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return JSON.stringify(v);
}

export function parsePreview(raw: Record<string, unknown> | null | undefined): InputPreview {
  const blocks: PreviewBlock[] = [];
  const rawBlocks = Array.isArray(raw?.blocks) ? raw.blocks : [];
  for (const b of rawBlocks) {
    if (typeof b !== 'object' || b === null) continue;
    const block = b as Record<string, unknown>;
    if (block.type === 'text') blocks.push({ type: 'text', text: str(block.text) });
    else if (block.type === 'keyValue' && Array.isArray(block.items)) {
      blocks.push({
        type: 'keyValue',
        items: block.items.map((i) => {
          const item = (i ?? {}) as Record<string, unknown>;
          return { label: str(item.label), value: str(item.value) };
        }),
      });
    } else if (block.type === 'table' && Array.isArray(block.columns) && Array.isArray(block.rows)) {
      blocks.push({
        type: 'table',
        columns: block.columns.map(str),
        rows: block.rows.map((r) => (Array.isArray(r) ? r.map(str) : [str(r)])),
      });
    }
  }
  const choices: PreviewChoice[] = (Array.isArray(raw?.choices) ? raw.choices : []).flatMap((c) => {
    if (typeof c !== 'object' || c === null) return [];
    const choice = c as Record<string, unknown>;
    if (typeof choice.value !== 'string') return [];
    return [{ value: choice.value, label: str(choice.label) || choice.value, style: str(choice.style) || 'secondary' }];
  });
  return {
    blocks,
    choices,
    consequence: typeof raw?.consequence === 'string' ? raw.consequence : null,
  };
}

export function PreviewBlocks({ blocks, caption }: { blocks: PreviewBlock[]; caption: string }) {
  if (blocks.length === 0) return null;
  return (
    <div className="stack-sm">
      {blocks.map((block, i) => {
        if (block.type === 'text') {
          return (
            <p key={i} className="passage" style={{ borderLeftColor: 'var(--color-border)', fontSize: 14, lineHeight: '20px' }}>
              {block.text}
            </p>
          );
        }
        if (block.type === 'keyValue') {
          return (
            <dl key={i} className="kv">
              {block.items.map((item, j) => (
                <div key={j} style={{ display: 'contents' }}>
                  <dt>{item.label}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>
          );
        }
        return (
          <div key={i} className="table-wrap">
            <table className="data-table">
              <caption className="sr-only">
                {caption} {i + 1}
              </caption>
              <thead>
                <tr>
                  {block.columns.map((c) => (
                    <th key={c} scope="col">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, r) => (
                  <tr key={r}>
                    {row.map((cell, c) => (
                      <td key={c} className={/^•+/.test(cell) ? 'mono' : undefined}>
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}
