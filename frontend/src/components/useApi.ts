import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/client";

export type Loadable<T> =
  | { state: "loading" }
  | { state: "data"; data: T }
  | { state: "error"; error: ApiError | Error };

export function useApi<T>(fetcher: () => Promise<T>, deps: readonly unknown[] = []): [Loadable<T>, () => void] {
  const [value, setValue] = useState<Loadable<T>>({ state: "loading" });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setValue((prev) => (prev.state === "data" ? prev : { state: "loading" }));
    fetcher().then(
      (data) => alive && setValue({ state: "data", data }),
      (error: unknown) => alive && setValue({ state: "error", error: error instanceof Error ? error : new Error("unknown") }),
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return [value, reload];
}
