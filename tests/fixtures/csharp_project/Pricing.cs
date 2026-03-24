using System;

namespace Example
{
    /// <summary>Pricing module — fixture for tests.</summary>
    public class Pricing
    {
        /// <summary>Calculate final price including discount.</summary>
        public double CalculatePrice(int itemId, int qty)
        {
            double basePrice = GetBasePrice(itemId);
            if (qty > 100)
            {
                basePrice = ApplyDiscount(basePrice, 10.0);
            }
            return basePrice * qty;
        }

        private double ApplyDiscount(double price, double pct)
        {
            return price * (1.0 - pct / 100.0);
        }

        private double GetBasePrice(int itemId)
        {
            return itemId * 10.0;
        }
    }
}
