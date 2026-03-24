/// Pricing module — fixture for tests.

/// Calculate final price including discount.
pub fn calculate_price(item_id: u32, qty: u32) -> f64 {
    let base = get_base_price(item_id);
    let base = if qty > 100 {
        apply_discount(base, 10.0)
    } else {
        base
    };
    base * qty as f64
}

/// Apply percentage discount (private).
fn apply_discount(price: f64, pct: f64) -> f64 {
    price * (1.0 - pct / 100.0)
}

fn get_base_price(item_id: u32) -> f64 {
    item_id as f64 * 10.0
}
