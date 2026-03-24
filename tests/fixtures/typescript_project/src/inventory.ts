/**
 * Inventory module — fixture for tests.
 */

import { calculatePrice } from './pricing';

export function checkStock(itemId: number, qty: number): boolean {
  const cost = calculatePrice(itemId, qty);
  return cost > 0;
}

export function reserveItems(itemId: number, qty: number): object {
  return { itemId, qty, reserved: true };
}
