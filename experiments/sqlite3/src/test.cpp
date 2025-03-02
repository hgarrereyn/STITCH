
extern "C" {
    #include "sqlite3.h"
}

#include <cstdio>
int main() {
    sqlite3 *db;
    int rc = sqlite3_open("test.db", &db);
    if (rc != SQLITE_OK) {
        fprintf(stderr, "Can't open database: %s\n", sqlite3_errmsg(db));
        sqlite3_close(db);
        return 1;
    }
    sqlite3_exec(db, "CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)", NULL, NULL, NULL);
    sqlite3_exec(db, "INSERT INTO test (name) VALUES ('John')", NULL, NULL, NULL);
    sqlite3_exec(db, "INSERT INTO test (name) VALUES ('Jane')", NULL, NULL, NULL);
    sqlite3_exec(db, "SELECT * FROM test", NULL, NULL, NULL);
    sqlite3_close(db);
    return 0;
}
