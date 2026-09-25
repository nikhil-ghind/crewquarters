import { Server } from 'lucide-react';
import { useEffect, useMemo } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router';
import { useSettings } from '../../api/queries';
import { Banner, ErrorPanel, SkeletonBlock } from '../../components/Feedback';
import { useDocumentTitle } from '../../components/Layout';
import { Stepper, type StepItem } from '../../components/Stepper';
import { useMe } from '../../shell/AuthGate';
import {
  firstIncomplete,
  isStepId,
  readSetupState,
  SETUP_STEPS,
  type SetupStepId,
} from './setupState';
import {
  AgentsStep,
  ConnectionsStep,
  ModelStep,
  OwnerStep,
  PreflightStep,
  ServicesStep,
  StorageStep,
  ValidationStep,
  WelcomeStep,
} from './steps';

const ANONYMOUS_STEPS: SetupStepId[] = ['welcome', 'preflight', 'owner'];

/**
 * Resumable first-run wizard (section 13.4). Progress is saved on the server after
 * each step; a refresh or the Google OAuth round trip resumes here.
 */
export default function SetupWizard() {
  const params = useParams();
  const navigate = useNavigate();
  const { status, query: me } = useMe();
  const signedIn = status === 'authenticated';
  const settings = useSettings({ enabled: signedIn });
  const state = useMemo(() => readSetupState(settings.data), [settings.data]);
  const requested = isStepId(params.step) ? params.step : undefined;
  useDocumentTitle('Set up Crewquarters');

  const resumeAt: SetupStepId = signedIn ? (state.current ?? firstIncomplete({ ...state, completed: [...state.completed, 'welcome', 'preflight', 'owner'] })) : 'welcome';
  const step: SetupStepId = requested ?? resumeAt;
  const blockedForAnonymous = !signedIn && !ANONYMOUS_STEPS.includes(step);

  useEffect(() => {
    if (!requested && (signedIn ? settings.isSuccess : status === 'anonymous')) {
      void navigate(`/setup/${resumeAt}${window.location.search}`, { replace: true });
    }
  }, [requested, signedIn, settings.isSuccess, status, resumeAt, navigate]);

  if (me.isPending || (signedIn && settings.isPending)) {
    return (
      <main className="bare-main" id="main">
        <SkeletonBlock label="Loading setup" />
      </main>
    );
  }
  if (signedIn && settings.data?.setupCompleted) return <Navigate to="/" replace />;

  const done = new Set<SetupStepId>(signedIn ? [...state.completed, 'welcome', 'preflight', 'owner'] : []);
  const skipped = new Set(state.skipped);
  const steps: StepItem[] = SETUP_STEPS.map((s) => {
    const optional = 'optional' in s && s.optional;
    let stepState: StepItem['state'] = 'upcoming';
    if (s.id === step) stepState = 'current';
    else if (done.has(s.id)) stepState = 'completed';
    else if (skipped.has(s.id)) stepState = 'optional';
    else if (!signedIn && !ANONYMOUS_STEPS.includes(s.id)) stepState = 'blocked';
    else if (optional) stepState = 'optional';
    return {
      id: s.id,
      label: s.label,
      state: stepState,
      meta: skipped.has(s.id) ? 'Skipped for now' : optional && stepState === 'optional' ? 'Optional' : undefined,
    };
  });

  const go = (id: string) => navigate(`/setup/${id}`);

  return (
    <div className="bare-layout">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="bare-header">
        <span className="brand-mark" aria-hidden="true">
          <Server size={18} />
        </span>
        <span className="brand-name">Crewquarters setup</span>
      </header>
      <main id="main" className="bare-main" tabIndex={-1}>
        <div className="setup-layout">
          <Stepper steps={steps} label="Setup steps" onSelect={go} />
          <div className="stack-lg" style={{ minWidth: 0 }}>
            {settings.isError ? <ErrorPanel error={settings.error} title="Could not load setup progress" onRetry={() => void settings.refetch()} /> : null}
            {blockedForAnonymous ? (
              <Banner tone="info" title="Create the owner account first">
                This step needs the owner account. <a href="/setup/owner">Go to Owner account</a>, or sign in if it already
                exists.
              </Banner>
            ) : (
              <StepBody step={step} signedIn={signedIn} />
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

function StepBody({ step, signedIn }: { step: SetupStepId; signedIn: boolean }) {
  switch (step) {
    case 'welcome':
      return <WelcomeStep />;
    case 'preflight':
      return <PreflightStep signedIn={signedIn} />;
    case 'owner':
      return <OwnerStep signedIn={signedIn} />;
    case 'storage':
      return <StorageStep />;
    case 'services':
      return <ServicesStep />;
    case 'model':
      return <ModelStep />;
    case 'connections':
      return <ConnectionsStep />;
    case 'agents':
      return <AgentsStep />;
    case 'validation':
      return <ValidationStep />;
  }
}
