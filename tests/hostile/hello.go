package main

import (
	"bufio"
	"fmt"
	"os"
	"sort"
	"strings"
)

func main() {
	reader := bufio.NewReader(os.Stdin)
	line, _ := reader.ReadString('\n')
	fmt.Println("hello from go")
	fmt.Println(strings.ToUpper(strings.TrimSpace(line)))
	v := []int{3, 1, 2}
	sort.Ints(v)
	fmt.Println(v)
}
