alter table dungeonshare.posts
  add column if not exists session_memory jsonb;

do $$
begin
  if not exists (
    select 1
      from pg_constraint
     where conname = 'dungeonshare_posts_session_memory_object_check'
       and conrelid = 'dungeonshare.posts'::regclass
  ) then
    alter table dungeonshare.posts
      add constraint dungeonshare_posts_session_memory_object_check
      check (
        session_memory is null
        or jsonb_typeof(session_memory) = 'object'
      );
  end if;
end
$$;
