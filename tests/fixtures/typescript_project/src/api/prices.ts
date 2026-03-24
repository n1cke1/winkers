/**
 * Prices API — fixture for tests.
 */

import { calculatePrice } from '../pricing';

export function getPrice(itemId: number, qty: number): object {
  const total = calculatePrice(itemId, qty);
  return { itemId, qty, total };
}
