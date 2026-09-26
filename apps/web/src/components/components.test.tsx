import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/errors';
import type { JsonSchema } from '../lib/jsonSchema';
import { permissionItems } from '../lib/permissions';
import { MODEL_MEMORY_STATUS, RUN_STATUS } from '../lib/status';
import { Button } from './Button';
import { DataTable } from './DataTable';
import { ConfirmDialog } from './Dialog';
import { EmptyState, ErrorPanel } from './Feedback';
import { LocalityChip } from './LocalityChip';
import { LogViewer } from './LogViewer';
import { Progress, ResourceMeter } from './Meters';
import { PermissionList } from './PermissionRow';
import { SchemaForm } from './SchemaForm';
import { SourceDrawer } from './SourceDrawer';
import { StatusBadge } from './StatusBadge';
import { Stepper, Timeline } from './Stepper';

describe('StatusBadge and LocalityChip', () => {
  it('shows text, not color alone', () => {
    render(<StatusBadge status={RUN_STATUS.WAITING_INPUT} context="Run state" />);
    expect(screen.getByText('Needs your input')).toBeInTheDocument();
    expect(screen.getByText('Run state:')).toHaveClass('sr-only');
  });
  it('labels local and cloud processing explicitly', () => {
    render(
      <>
        <LocalityChip provider="local" long />
        <LocalityChip provider="anthropic" />
      </>,
    );
    expect(screen.getByText('Local on this device')).toBeInTheDocument();
    expect(screen.getByText('Cloud · Anthropic')).toBeInTheDocument();
  });
});

describe('Button', () => {
  it('explains why it is disabled, linked for assistive technology', () => {
    render(<Button disabledReason="Install a local model first.">Enable local chat</Button>);
    const button = screen.getByRole('button', { name: 'Enable local chat' });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription('Install a local model first.');
  });
  it('shows a busy label', () => {
    render(
      <Button busy busyLabel="Submitting…">
        Approve 3 calls
      </Button>,
    );
    expect(screen.getByRole('button', { name: 'Submitting…' })).toHaveAttribute('aria-busy', 'true');
  });
});

describe('meters and progress', () => {
  it('exposes a meter with a text equivalent', () => {
    render(<ResourceMeter label="Unified memory" value={112} max={128} valueText="112 GiB of 128 GiB used" warnAt={0.85} />);
    const meter = screen.getByRole('meter', { name: 'Unified memory' });
    expect(meter).toHaveAttribute("aria-valuetext", "112 GiB of 128 GiB used");
    expect(meter.firstElementChild).toHaveClass('tone-warning-fill');
  });
  it('is determinate only with a real percentage', () => {
    const { rerender } = render(<Progress label="Downloading" percent={42} />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '42');
    rerender(<Progress label="Loading model" stage="Loading weights" since={new Date().toISOString()} />);
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.getByText('Loading weights')).toBeInTheDocument();
    expect(screen.getByText(/elapsed/)).toBeInTheDocument();
  });
});

describe('DataTable', () => {
  const rows = [
    { id: 'a', name: 'Beta', n: 2 },
    { id: 'b', name: 'Alpha', n: 1 },
  ];
  const columns = [
    { key: 'name', header: 'Name', cell: (r: (typeof rows)[number]) => r.name, sortValue: (r: (typeof rows)[number]) => r.name },
    { key: 'n', header: 'Count', cell: (r: (typeof rows)[number]) => String(r.n) },
  ];
  it('sorts by column and reports aria-sort', async () => {
    render(<DataTable caption="Things" columns={columns} rows={rows} rowKey={(r) => r.id} />);
    await userEvent.click(screen.getByRole('button', { name: /Name/ }));
    expect(screen.getByRole('columnheader', { name: /Name/ })).toHaveAttribute('aria-sort', 'ascending');
    const cells = screen.getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell')[0]?.textContent);
    expect(cells).toEqual(['Alpha', 'Beta']);
  });
  it('renders the empty state', () => {
    render(<DataTable caption="Things" columns={columns} rows={[]} rowKey={(r) => r.id} empty={<EmptyState title="Nothing yet" />} />);
    expect(screen.getByText('Nothing yet')).toBeInTheDocument();
  });
  it('becomes a card list below 768 px', () => {
    const original = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    render(<DataTable caption="Things" columns={columns} rows={rows} rowKey={(r) => r.id} />);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Things' })).toBeInTheDocument();
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: original });
  });
});

describe('Stepper and Timeline', () => {
  it('marks the current step and states each status in words', () => {
    render(
      <Stepper
        label="Setup"
        steps={[
          { id: 'a', label: 'Welcome', state: 'completed' },
          { id: 'b', label: 'Owner', state: 'current' },
          { id: 'c', label: 'Model', state: 'optional' },
          { id: 'd', label: 'Agents', state: 'blocked' },
        ]}
      />,
    );
    const current = screen.getByRole('listitem', { current: 'step' });
    expect(current).toHaveTextContent('Owner');
    expect(screen.getByRole('button', { name: 'Welcome (Completed)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Agents (Blocked)' })).toBeDisabled();
  });
  it('emphasizes the current timeline entry', () => {
    render(
      <Timeline
        label="Run"
        entries={[
          { key: 1, title: 'Preparing agent', tone: 'info', icon: 'spinner' },
          { key: 2, title: 'Needs your input', tone: 'warning', icon: 'hand', current: true },
        ]}
      />,
    );
    expect(screen.getByRole('listitem', { current: 'step' })).toHaveTextContent('Needs your input');
  });
});

describe('SchemaForm', () => {
  const schema: JsonSchema = {
    type: 'object',
    required: ['spreadsheetId'],
    properties: {
      spreadsheetId: { type: 'string', 'x-crewquarters-widget': 'spreadsheet', description: 'Sheet to read.' },
      maxCalls: { type: 'integer', minimum: 1, maximum: 10 },
      mode: { type: 'string', enum: ['fast', 'careful'] },
      apiKey: { type: 'string', writeOnly: true },
      batchSize: { type: 'integer', 'x-crewquarters-group': 'Advanced' },
    },
  };
  function Harness({ disabled = false }: { disabled?: boolean }) {
    const [value, setValue] = useState<Record<string, unknown>>({});
    return (
      <>
        <SchemaForm
          schema={schema}
          value={value}
          onChange={setValue}
          disabled={disabled}
          errors={[{ path: '/spreadsheetId', message: 'Spreadsheet ID is required.' }]}
        />
        <output data-testid="value">{JSON.stringify(value)}</output>
      </>
    );
  }
  it('renders labelled widgets with errors and writes typed values', async () => {
    render(<Harness />);
    const sheet = screen.getByRole('textbox', { name: /Spreadsheet id/ });
    expect(sheet).toHaveAttribute('aria-invalid', 'true');
    expect(sheet).toHaveAccessibleDescription(/Sheet to read\./);
    expect(sheet).toHaveAccessibleDescription(/Spreadsheet ID is required\./);
    await userEvent.type(screen.getByRole('spinbutton', { name: /Max calls/ }), '4');
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /Mode/ }), 'careful');
    expect(screen.getByLabelText(/Api key/)).toHaveAttribute('type', 'password');
    expect(JSON.parse(screen.getByTestId('value').textContent ?? '{}')).toEqual({ maxCalls: 4, mode: 'careful' });
    expect(screen.getByText('Advanced settings')).toBeInTheDocument();
  });
  it('disables every control', () => {
    render(<Harness disabled />);
    expect(screen.getByRole('textbox', { name: /Spreadsheet id/ })).toBeDisabled();
  });
});

describe('PermissionList', () => {
  it('requires an approval per capability and highlights cloud and phone', async () => {
    const onApprove = vi.fn();
    const items = permissionItems({ connectors: { twilio: ['call.fixed_script'] }, cloudProviders: ['openai'], userInput: true });
    render(<PermissionList items={items} approvals={{}} onApprove={onApprove} changedIds={new Set(['cloud.openai'])} />);
    const boxes = screen.getAllByRole('checkbox');
    expect(boxes).toHaveLength(3);
    await userEvent.click(screen.getByRole('checkbox', { name: /Approve\s?: Send data to OpenAI/ }));
    expect(onApprove).toHaveBeenCalledWith('cloud.openai', true);
    expect(screen.getByText('New in this version')).toBeInTheDocument();
    expect(screen.getByText('Place phone calls with a fixed script').closest('li')).toHaveAttribute('data-emphasis', 'phone');
  });

  it('describes the camera and owner-email permissions', () => {
    const items = permissionItems({ camera: ['config'], connectors: { google: ['gmail.send'] } });
    expect(items.map((i) => [i.id, i.capability])).toEqual([
      ['camera.snapshot:config', 'Take snapshots from the camera you configure'],
      ['google.gmail.send', 'Email alerts to your own Gmail address'],
    ]);
  });
});

describe('ConfirmDialog', () => {
  it('names the action and consequence, and closes with Escape unless busy', async () => {
    const onCancel = vi.fn();
    const { rerender } = render(
      <ConfirmDialog open title="Place a live test call?" consequence="This is a real call." confirmLabel="Place test call" onConfirm={() => undefined} onCancel={onCancel} />,
    );
    const dialog = screen.getByRole('dialog', { name: 'Place a live test call?' });
    expect(dialog).toHaveAccessibleDescription('This is a real call.');
    expect(within(dialog).getByRole('button', { name: 'Place test call' })).toBeInTheDocument();
    dialog.dispatchEvent(new Event('cancel', { cancelable: true }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    rerender(
      <ConfirmDialog open busy title="Place a live test call?" consequence="This is a real call." confirmLabel="Place test call" onConfirm={() => undefined} onCancel={onCancel} />,
    );
    screen.getByRole('dialog').dispatchEvent(new Event('cancel', { cancelable: true }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

describe('ErrorPanel', () => {
  it('leads with remediation and shows a copyable diagnostic code', () => {
    render(<ErrorPanel error={new ApiError(503, 'MODEL_GATEWAY_UNAVAILABLE', 'upstream failed', 'req-9')} title="Chat could not start" onRetry={() => undefined} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Model serving is unavailable. Check System status.');
    expect(screen.getByText('MODEL_GATEWAY_UNAVAILABLE · req-9')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

describe('safe rendering', () => {
  it('LogViewer escapes markup and filters by level', async () => {
    const { container } = render(
      <LogViewer
        entries={[
          { id: 1, time: '2026-09-25T04:30:00Z', level: 'info', message: '<img src=x onerror=alert(1)>' },
          { id: 2, time: '2026-09-25T04:30:01Z', level: 'error', message: 'boom' },
        ]}
      />,
    );
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Level'), 'error');
    expect(screen.queryByText('<img src=x onerror=alert(1)>')).not.toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
  });
  it('SourceDrawer shows the passage as text', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 });
    const { container } = render(
      <MemoryRouter>
        <SourceDrawer
          onClose={() => undefined}
          citation={{
            index: 1,
            citationId: 'c1',
            text: '<script>alert("x")</script> Refunds within 14 days.',
            document: { id: 'd1', name: 'policy.md' },
            locator: {},
            location: 'Refunds (line 4)',
            knowledgeBaseId: 'kb1',
          }}
        />
      </MemoryRouter>,
    );
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByText(/<script>alert\("x"\)<\/script> Refunds/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open document' })).toHaveAttribute('href', '/knowledge/kb1?document=d1');
    expect(MODEL_MEMORY_STATUS.READY.label).toBe('Ready');
  });
});
