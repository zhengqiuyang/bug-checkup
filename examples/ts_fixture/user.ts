import { parseImage } from "./mod";

export function run(source: string) {
  const one = parseImage(source);
  return parseImage(one);
}