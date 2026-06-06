import { useCallback, useEffect, useState } from "react";
import { pulseApi } from "../services/apiClient";

export function useApiResource<T>(endpoint: string) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    setError("");
    const value = await pulseApi<T>(endpoint);
    setData(value);
  }, [endpoint]);

  useEffect(() => {
    let active = true;
    reload()
      .catch(errorValue => active && setError(errorValue instanceof Error ? errorValue.message : "Request failed."))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [reload]);

  return { data, loading, error, reload };
}
