// Package inventory — fixture for tests.
package inventory

import "go_project/pricing"

// CheckStock checks stock and returns price.
func CheckStock(itemID int, qty int) float64 {
	return pricing.CalculatePrice(itemID, qty)
}

// ReserveItems reserves items in inventory.
func ReserveItems(itemID int, qty int) bool {
	return qty > 0
}
