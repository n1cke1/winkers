package com.example;

/** Inventory module — fixture for tests. */
public class Inventory {

    private Pricing pricing = new Pricing();

    /** Check stock and return price. */
    public double checkStock(int itemId, int qty) {
        return pricing.calculatePrice(itemId, qty);
    }

    /** Reserve items in inventory. */
    public boolean reserveItems(int itemId, int qty) {
        return qty > 0;
    }
}
