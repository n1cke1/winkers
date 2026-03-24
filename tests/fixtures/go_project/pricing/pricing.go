// Package pricing — fixture for tests.
package pricing

// CalculatePrice returns the final price for an item.
func CalculatePrice(itemID int, qty int) float64 {
	base := getBasePrice(itemID)
	if qty > 100 {
		base = applyDiscount(base, 10.0)
	}
	return base * float64(qty)
}

// applyDiscount applies a percentage discount (unexported).
func applyDiscount(price float64, pct float64) float64 {
	return price * (1.0 - pct/100.0)
}

func getBasePrice(itemID int) float64 {
	return float64(itemID) * 10.0
}
