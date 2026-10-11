/** Development status only. This label never grants device or vehicle execution. */
export function obdFunctionLabel(name: string, implemented: boolean): string {
  return implemented ? name : `${name}(未完成)`;
}
