import { X } from 'lucide-react';
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

interface ToastItem {
  id: number;
  message: string;
  action?: { label: string; onClick: () => void };
}

interface Feedback {
  /**
   * Non-critical success/undo feedback. Never the only place an error appears
   * (section 13.15): errors are rendered inline where they happened.
   */
  toast: (message: string, action?: ToastItem['action']) => void;
  /** Screen-reader announcement; assertive only for blocking failures. */
  announce: (message: string, politeness?: 'polite' | 'assertive') => void;
}

const FeedbackContext = createContext<Feedback | null>(null);

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [polite, setPolite] = useState('');
  const [assertive, setAssertive] = useState('');
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((items) => items.filter((t) => t.id !== id));
  }, []);

  const announce = useCallback((message: string, politeness: 'polite' | 'assertive' = 'polite') => {
    const set = politeness === 'assertive' ? setAssertive : setPolite;
    // Clear first so repeating the same message is announced again.
    set('');
    window.setTimeout(() => set(message), 50);
  }, []);

  const toast = useCallback(
    (message: string, action?: ToastItem['action']) => {
      const id = nextId.current++;
      setToasts((items) => [...items.slice(-2), { id, message, action }]);
      window.setTimeout(() => dismiss(id), action ? 8000 : 5000);
    },
    [dismiss],
  );

  const value = useMemo(() => ({ toast, announce }), [toast, announce]);

  return (
    <FeedbackContext.Provider value={value}>
      {children}
      <div className="sr-only" aria-live="polite" aria-atomic="true" data-testid="live-polite">
        {polite}
      </div>
      <div className="sr-only" aria-live="assertive" aria-atomic="true">
        {assertive}
      </div>
      <div className="toast-region" aria-live="polite" aria-relevant="additions">
        {toasts.map((t) => (
          <div key={t.id} className="toast" role="status">
            <span style={{ flex: 1 }}>{t.message}</span>
            {t.action ? (
              <button
                type="button"
                className="btn btn-tertiary"
                onClick={() => {
                  t.action?.onClick();
                  dismiss(t.id);
                }}
              >
                {t.action.label}
              </button>
            ) : null}
            <button
              type="button"
              className="btn btn-tertiary btn-icon"
              aria-label="Dismiss notification"
              onClick={() => dismiss(t.id)}
            >
              <X size={16} aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </FeedbackContext.Provider>
  );
}

export function useFeedback(): Feedback {
  const ctx = useContext(FeedbackContext);
  if (!ctx) throw new Error('useFeedback needs FeedbackProvider');
  return ctx;
}
