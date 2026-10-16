import { useEffect, useState } from "react";
import { folded, onFolds } from "./viewport";

/** useFolded is whether the layout has given up what the named fold gives up. */
export function useFolded(name: string): boolean {
  const [now, setNow] = useState(() => folded(name));
  useEffect(() => {
    setNow(folded(name));
    return onFolds((f) => setNow(f.split(" ").includes(name)));
  }, [name]);
  return now;
}
