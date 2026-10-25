import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { ApiError } from "../api/client";
import { t } from "../i18n";

interface ToastItem {
  id: number;
  text: string;
  errorId: string | null;
  critical: boolean;
}

interface ToastValue {
  error: (err: unknown) => void;
  info: (text: string) => void;
}

const ToastContext = createContext<ToastValue | null>(null);
let counter = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const push = useCallback((item: Omit<ToastItem, "id">) => {
    counter += 1;
    const id = counter;
    setItems((current) => [...current, { ...item, id }]);
    window.setTimeout(() => setItems((current) => current.filter((x) => x.id !== id)), item.critical ? 15000 : 6000);
  }, []);

  const error = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError) {
        push({ text: err.message, errorId: err.errorId, critical: err.severity === "critical" });
      } else {
        push({ text: "Kuch gadbad hui. Dobara try karein.", errorId: null, critical: false });
      }
    },
    [push],
  );
  const info = useCallback((text: string) => push({ text, errorId: null, critical: false }), [push]);

  return (
    <ToastContext.Provider value={{ error, info }}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((item) => (
          <div key={item.id} className={item.critical ? "toast toast-critical" : "toast"}>
            <span>{item.text}</span>
            {item.errorId && (
              <small>
                {t.errorIdLabel}: {item.errorId}
              </small>
            )}
            {item.critical && item.errorId && (
              <button type="button" onClick={() => void navigator.clipboard.writeText(item.errorId ?? "")}>
                {t.copyErrorId}
              </button>
            )}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastValue {
  const value = useContext(ToastContext);
  if (!value) throw new Error("ToastProvider missing");
  return value;
}
