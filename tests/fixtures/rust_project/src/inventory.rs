/// Inventory module — fixture for tests.
use crate::pricing;

/// Check stock and return price.
pub fn check_stock(item_id: u32, qty: u32) -> f64 {
    pricing::calculate_price(item_id, qty)
}

/// Reserve items in inventory.
pub fn reserve_items(item_id: u32, qty: u32) -> bool {
    qty > 0
}
