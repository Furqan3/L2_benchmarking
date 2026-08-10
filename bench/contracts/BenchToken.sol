// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Workload token for the L2 finality benchmark (task B1 step 2).
//
// Deliberately the plainest possible OpenZeppelin ERC-20: no hooks, no pausing,
// no permit, no access control. The study measures what it costs a rollup to
// settle an ordinary token transfer, so anything unusual in this contract would
// show up in the gas and DA figures as if it were a property of the rollup.
//
// The OpenZeppelin sources under vendor/ are pinned at 5.0.2 and committed, for
// the same reason the ABIs are: a result should not stop being reproducible
// because a registry moved.
//
// The whole supply is minted to the deployer, who is the benchmark account. One
// token unit per transfer against a supply of 10^24 means the balance survives
// the entire experiment matrix many times over.

import "vendor/@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract BenchToken is ERC20 {
    constructor() ERC20("Bench Token", "BENCH") {
        _mint(msg.sender, 1_000_000 * 10 ** decimals());
    }
}
