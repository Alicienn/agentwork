//! Locked acceptance tests for WP01 (listed in `locked_tests:`; agents must not edit them).

use rust_demo::checked_sum;

#[test]
fn sums_small_values() {
    assert_eq!(checked_sum(&[1, 2, 3]), Some(6));
}

#[test]
fn empty_slice_is_zero() {
    assert_eq!(checked_sum(&[]), Some(0));
}

#[test]
fn overflow_is_none() {
    assert_eq!(checked_sum(&[i64::MAX, 1]), None);
}
