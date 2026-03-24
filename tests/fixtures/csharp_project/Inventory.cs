using System;
using Example;

namespace Example
{
    /// <summary>Inventory module — fixture for tests.</summary>
    public class Inventory
    {
        private Pricing _pricing = new Pricing();

        /// <summary>Check stock and return price.</summary>
        public double CheckStock(int itemId, int qty)
        {
            return _pricing.CalculatePrice(itemId, qty);
        }

        /// <summary>Reserve items in inventory.</summary>
        public bool ReserveItems(int itemId, int qty)
        {
            return qty > 0;
        }
    }
}
