/** The id of a change's Accept button, which takes focus when it's the next to decide. */
export function acceptId(changeSetId: string, position: number): string {
  return `${changeSetId}-change-${String(position)}-accept`;
}
