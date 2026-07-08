use std::io::Read;

fn main() {
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input).ok();
    println!("hello from rust");
    println!("{}", input.trim().to_uppercase());
    let mut v = vec![3, 1, 2];
    v.sort();
    println!("{:?}", v);
}
