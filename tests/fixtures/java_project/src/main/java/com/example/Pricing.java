package com.example;

/** Pricing module — fixture for tests. */
public class Pricing {

    /** Calculate final price including discount. */
    public double calculatePrice(int itemId, int qty) {
        double base = getBasePrice(itemId);
        if (qty > 100) {
            base = applyDiscount(base, 10.0);
        }
        return base * qty;
    }

    /** Apply percentage discount to price. */
    private double applyDiscount(double price, double pct) {
        return price * (1.0 - pct / 100.0);
    }

    private double getBasePrice(int itemId) {
        return itemId * 10.0;
    }
}
