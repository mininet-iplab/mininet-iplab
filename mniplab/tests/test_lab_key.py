"""Unit tests for Lab key allocation, derivation, and validation."""

import unittest
from mniplab.lab_key import (
    LabKeyAllocator,
    derive_lab_key,
    is_valid_lab_key,
    validate_lab_key,
)


class TestLabKey(unittest.TestCase):
    """Test suite for Lab key generation and allocation."""

    def test_derive_lab_key_deterministic(self) -> None:
        """Deriving a key from the same script and PID produces the identical key."""
        key1 = derive_lab_key("static-lab.py", 1234)
        key2 = derive_lab_key("static-lab.py", 1234)
        self.assertEqual(key1, key2)
        self.assertTrue(is_valid_lab_key(key1))

    def test_derive_lab_key_varies_with_input(self) -> None:
        """Different PIDs or scripts produce different keys."""
        key1 = derive_lab_key("static-lab.py", 1234)
        key2 = derive_lab_key("static-lab.py", 1235)
        key3 = derive_lab_key("ospf-lab.py", 1234)
        self.assertNotEqual(key1, key2)
        self.assertNotEqual(key1, key3)

    def test_lab_key_validity(self) -> None:
        """Keys must be short lowercase alphanumeric strings safe in filenames and interface names."""
        self.assertTrue(is_valid_lab_key("k01"))
        self.assertTrue(is_valid_lab_key("a1b2"))
        self.assertTrue(is_valid_lab_key("lab1"))

        # Invalid keys: too long, spaces, uppercase, symbols, slashes
        self.assertFalse(is_valid_lab_key(""))
        self.assertFalse(is_valid_lab_key("toolongkey123"))
        self.assertFalse(is_valid_lab_key("Lab1"))
        self.assertFalse(is_valid_lab_key("k/1"))
        self.assertFalse(is_valid_lab_key("k 1"))
        self.assertFalse(is_valid_lab_key("k:1"))
        self.assertFalse(is_valid_lab_key("k.1"))

    def test_allocator_allocate_and_release(self) -> None:
        """Allocator tracks active keys and frees them on release."""
        allocator = LabKeyAllocator()
        key = allocator.allocate(key="test1")
        self.assertEqual(key, "test1")

        # Allocating the same active key should fail
        with self.assertRaises(ValueError):
            allocator.allocate(key="test1")

        # After releasing, it can be allocated again
        allocator.release("test1")
        key_again = allocator.allocate(key="test1")
        self.assertEqual(key_again, "test1")
        allocator.release("test1")

    def test_allocator_collision_diversification(self) -> None:
        """When derived key collides, allocator allocates a distinct available key."""
        allocator = LabKeyAllocator()
        key1 = allocator.allocate(script="test.py", pid=100)
        key2 = allocator.allocate(script="test.py", pid=100)
        self.assertNotEqual(key1, key2)
        self.assertTrue(is_valid_lab_key(key1))
        self.assertTrue(is_valid_lab_key(key2))
        allocator.release(key1)
        allocator.release(key2)



    def test_derived_keys_stay_four_characters_under_collision(self) -> None:
        """A derived key never grows: it lives inside a 15-character interface name."""
        allocator = LabKeyAllocator()
        keys = {allocator.allocate(script="lab.py", pid=42) for _ in range(40)}
        self.assertEqual(len(keys), 40)
        self.assertTrue(all(len(k) == 4 for k in keys), keys)


if __name__ == "__main__":
    unittest.main()
