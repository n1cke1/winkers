/**
 * Pricing module — fixture for tests.
 */

export function calculatePrice(itemId: number, qty: number): number {
  const base = getBasePrice(itemId);
  return base * qty;
}

export function applyDiscount(price: number, pct: number): number {
  return price * (1 - pct / 100);
}

function getBasePrice(itemId: number): number {
  const prices: Record<number, number> = { 1: 10, 2: 20, 3: 5 };
  return prices[itemId] ?? 0;
}
