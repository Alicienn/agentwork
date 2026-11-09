# rust-demo design

`checked_sum(values: &[i64]) -> Option<i64>` returns the sum of `values`, or `None`
when the sum overflows `i64`. The empty slice sums to `Some(0)`.
